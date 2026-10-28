//! Coeur Rust optionnel du pont ACP (Agent Client Protocol).
//!
//! Le kit est un **client** ACP : l'agent de l'hote tourne en sous-processus
//! et parle JSON-RPC 2.0 sur stdio. Ce crate porte la partie **pure** du
//! client — celle qui ne lit ni ne n'ecrit rien :
//!
//! - la validation des messages (`initialize`, `session/new`, `session/load`,
//!   `session/prompt`, `session/cancel`, `session/update`,
//!   `session/request_permission`) ;
//! - la machine d'etats d'une session ;
//! - le routage des demandes de permission (en attente, accordee, refusee,
//!   annulee).
//!
//! Le lancement du sous-processus, les lignes lues et ecrites, les threads et
//! l'invite au terminal restent en Python (`grimoire.acp`). Le repli Python
//! (`grimoire.acp.machine.PyMachine`) est celui qui est livre : la roue reste
//! `py3-none-any`. Ce crate est l'oracle de parite — memes transcriptions
//! JSON-RPC (`tests/fixtures/acp/`), memes evenements, memes etats.
//!
//! ## Le contrat
//!
//! [`Machine::on_outgoing`] recoit un message que le *client* envoie a
//! l'agent, [`Machine::on_incoming`] un message que l'agent envoie au client.
//! Chacun rend la liste des evenements normalises produits (objets JSON a
//! cle `type`). Un message invalide ou hors etat ne change **rien** a l'etat
//! et produit un evenement `error` : la machine ne leve jamais.

use serde_json::{json, Map, Value};

/// Version du contrat Rust/Python (evenements et instantane). A incrementer
/// si la forme des evenements change.
pub const ABI_VERSION: u32 = 1;

const STOP_REASONS: [&str; 5] = [
    "end_turn",
    "max_tokens",
    "max_turn_requests",
    "refusal",
    "cancelled",
];
const OPTION_KINDS: [&str; 4] = ["allow_once", "allow_always", "reject_once", "reject_always"];

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum State {
    New,
    Initializing,
    Ready,
    Opening,
    Idle,
    Prompting,
    Cancelling,
    Closed,
}

impl State {
    pub fn as_str(self) -> &'static str {
        match self {
            State::New => "new",
            State::Initializing => "initializing",
            State::Ready => "ready",
            State::Opening => "opening",
            State::Idle => "idle",
            State::Prompting => "prompting",
            State::Cancelling => "cancelling",
            State::Closed => "closed",
        }
    }
}

struct Outstanding {
    key: String,
    method: String,
}

struct Pending {
    key: String,
    request_id: Value,
    /// `None` : requete d'agent que le client n'annonce pas (`fs/*`,
    /// `terminal/*`) — elle recoit une erreur, pas une permission.
    options: Option<Vec<(String, String)>>,
}

struct Resolved {
    request_id: Value,
    outcome: &'static str,
    option_id: Option<String>,
}

pub struct Machine {
    state: State,
    session_id: Option<String>,
    opening_session: Option<String>,
    load_session: bool,
    outstanding: Vec<Outstanding>,
    pending: Vec<Pending>,
    resolved: Vec<Resolved>,
}

fn err(code: &str, message: &str) -> Value {
    json!({"type": "error", "code": code, "message": message})
}

fn id_ok(v: &Value) -> bool {
    v.is_string() || v.is_i64() || v.is_u64()
}

fn str_param<'a>(params: &'a Value, name: &str) -> Option<&'a str> {
    params.get(name).and_then(Value::as_str)
}

impl Default for Machine {
    fn default() -> Self {
        Self::new()
    }
}

impl Machine {
    pub fn new() -> Self {
        Machine {
            state: State::New,
            session_id: None,
            opening_session: None,
            load_session: false,
            outstanding: Vec::new(),
            pending: Vec::new(),
            resolved: Vec::new(),
        }
    }

    pub fn state(&self) -> State {
        self.state
    }

    /// Instantane complet, sans heure ni aleas : deux machines nourries des
    /// memes messages rendent le meme instantane.
    pub fn snapshot(&self) -> Value {
        let pending: Vec<Value> = self
            .pending
            .iter()
            .filter(|p| p.options.is_some())
            .map(|p| p.request_id.clone())
            .collect();
        let resolved: Vec<Value> = self
            .resolved
            .iter()
            .map(|r| {
                json!({
                    "request_id": r.request_id,
                    "outcome": r.outcome,
                    "option_id": r.option_id,
                })
            })
            .collect();
        let outstanding: Vec<Value> = self
            .outstanding
            .iter()
            .map(|o| Value::String(o.method.clone()))
            .collect();
        json!({
            "state": self.state.as_str(),
            "session_id": self.session_id,
            "load_session": self.load_session,
            "pending_permissions": pending,
            "resolved_permissions": resolved,
            "outstanding": outstanding,
        })
    }

    fn base_check(msg: &Value) -> Result<(), Value> {
        if !msg.is_object() {
            return Err(err("invalid_message", "le message n'est pas un objet JSON"));
        }
        if msg.get("jsonrpc").and_then(Value::as_str) != Some("2.0") {
            return Err(err("invalid_message", "jsonrpc doit valoir \"2.0\""));
        }
        if let Some(id) = msg.get("id") {
            if !id_ok(id) {
                return Err(err(
                    "invalid_message",
                    "id doit etre une chaine ou un entier",
                ));
            }
        }
        if let Some(m) = msg.get("method") {
            if !m.is_string() {
                return Err(err("invalid_message", "method doit etre une chaine"));
            }
        } else if msg.get("id").is_none()
            || !(msg.get("result").is_some() || msg.get("error").is_some())
        {
            return Err(err("invalid_message", "ni methode ni reponse"));
        }
        Ok(())
    }

    // ── client -> agent ──────────────────────────────────────────────────

    pub fn on_outgoing(&mut self, msg: &Value) -> Vec<Value> {
        if let Err(e) = Self::base_check(msg) {
            return vec![e];
        }
        match msg.get("method").and_then(Value::as_str) {
            Some(method) => self.out_request(method, msg),
            None => self.out_response(msg),
        }
    }

    fn out_request(&mut self, method: &str, msg: &Value) -> Vec<Value> {
        let null = Value::Null;
        let params = msg.get("params").unwrap_or(&null);
        let id = msg.get("id");
        if self.state == State::Closed {
            return vec![err("closed", "la session est fermee")];
        }
        if method != "session/cancel" && id.is_none() {
            return vec![err(
                "invalid_message",
                "cette methode est une requete : id manquant",
            )];
        }
        match method {
            "initialize" => {
                if self.state != State::New {
                    return vec![err("already_initialized", "initialize a deja ete envoye")];
                }
                if !params
                    .get("protocolVersion")
                    .is_some_and(|v| v.is_i64() || v.is_u64())
                {
                    return vec![err(
                        "invalid_message",
                        "initialize : protocolVersion entier requis",
                    )];
                }
                self.state = State::Initializing;
                self.track(id, method);
                vec![]
            }
            "session/new" | "session/load" => {
                match self.state {
                    State::Ready | State::Idle => {}
                    State::New | State::Initializing => {
                        return vec![err("not_initialized", "initialize n'est pas termine")];
                    }
                    _ => return vec![err("busy", "une operation est deja en cours")],
                }
                if str_param(params, "cwd").is_none() {
                    return vec![err("invalid_message", "cwd requis")];
                }
                if method == "session/load" {
                    if !self.load_session {
                        return vec![err("load_unsupported", "l'agent n'annonce pas loadSession")];
                    }
                    match str_param(params, "sessionId") {
                        Some(s) => self.opening_session = Some(s.to_string()),
                        None => return vec![err("invalid_message", "sessionId requis")],
                    }
                }
                self.state = State::Opening;
                self.track(id, method);
                vec![]
            }
            "session/prompt" => {
                match self.state {
                    State::Idle => {}
                    State::Prompting | State::Cancelling | State::Opening => {
                        return vec![err("busy", "un tour est deja en cours")];
                    }
                    _ => return vec![err("no_session", "aucune session ouverte")],
                }
                if str_param(params, "sessionId") != self.session_id.as_deref() {
                    return vec![err(
                        "session_mismatch",
                        "sessionId different de la session ouverte",
                    )];
                }
                if !params.get("prompt").is_some_and(Value::is_array) {
                    return vec![err("invalid_message", "prompt (liste de blocs) requis")];
                }
                self.state = State::Prompting;
                self.track(id, method);
                vec![]
            }
            "session/cancel" => {
                if id.is_some() {
                    return vec![err(
                        "invalid_message",
                        "session/cancel est une notification : pas d'id",
                    )];
                }
                if self.state != State::Prompting {
                    return vec![err("not_prompting", "aucun tour a annuler")];
                }
                if str_param(params, "sessionId") != self.session_id.as_deref() {
                    return vec![err(
                        "session_mismatch",
                        "sessionId different de la session ouverte",
                    )];
                }
                self.state = State::Cancelling;
                vec![json!({"type": "cancel_requested", "session_id": self.session_id})]
            }
            _ => vec![err("unknown_method", "methode client inconnue")],
        }
    }

    fn track(&mut self, id: Option<&Value>, method: &str) {
        if let Some(id) = id {
            self.outstanding.push(Outstanding {
                key: id.to_string(),
                method: method.to_string(),
            });
        }
    }

    fn out_response(&mut self, msg: &Value) -> Vec<Value> {
        let id = &msg["id"];
        let key = id.to_string();
        let Some(pos) = self.pending.iter().position(|p| p.key == key) else {
            return vec![err(
                "unknown_request",
                "aucune requete de l'agent avec cet id",
            )];
        };
        let Some(options) = self.pending[pos].options.clone() else {
            // Requete non supportee : la reponse attendue est une erreur.
            self.pending.remove(pos);
            return vec![json!({"type": "unsupported_replied", "request_id": id})];
        };
        let outcome = msg
            .pointer("/result/outcome/outcome")
            .and_then(Value::as_str);
        let (label, option_id): (&'static str, Option<String>) = match outcome {
            Some("cancelled") => ("cancelled", None),
            Some("selected") => {
                let Some(opt) = msg
                    .pointer("/result/outcome/optionId")
                    .and_then(Value::as_str)
                else {
                    return vec![err("invalid_permission_response", "optionId requis")];
                };
                let Some((_, kind)) = options.iter().find(|(oid, _)| oid == opt) else {
                    return vec![err(
                        "invalid_permission_response",
                        "optionId inconnu de la demande",
                    )];
                };
                (
                    if kind.starts_with("allow") {
                        "granted"
                    } else {
                        "denied"
                    },
                    Some(opt.to_string()),
                )
            }
            _ => {
                return vec![err(
                    "invalid_permission_response",
                    "outcome selected ou cancelled requis",
                )]
            }
        };
        let p = self.pending.remove(pos);
        self.resolved.push(Resolved {
            request_id: p.request_id.clone(),
            outcome: label,
            option_id: option_id.clone(),
        });
        vec![json!({
            "type": "permission_resolved",
            "request_id": p.request_id,
            "outcome": label,
            "option_id": option_id,
        })]
    }

    // ── agent -> client ──────────────────────────────────────────────────

    pub fn on_incoming(&mut self, msg: &Value) -> Vec<Value> {
        if let Err(e) = Self::base_check(msg) {
            return vec![e];
        }
        match msg.get("method").and_then(Value::as_str) {
            Some(method) => self.in_request(method, msg),
            None => self.in_response(msg),
        }
    }

    fn known_session(&self, sid: &str) -> bool {
        self.session_id.as_deref() == Some(sid) || self.opening_session.as_deref() == Some(sid)
    }

    fn in_request(&mut self, method: &str, msg: &Value) -> Vec<Value> {
        let null = Value::Null;
        let params = msg.get("params").unwrap_or(&null);
        let id = msg.get("id");
        match (method, id) {
            ("session/update", None) => {
                let Some(sid) = str_param(params, "sessionId") else {
                    return vec![err("invalid_message", "session/update : sessionId requis")];
                };
                let Some(kind) = params
                    .pointer("/update/sessionUpdate")
                    .and_then(Value::as_str)
                else {
                    return vec![err(
                        "invalid_message",
                        "session/update : update.sessionUpdate requis",
                    )];
                };
                if !self.known_session(sid) {
                    return vec![err(
                        "unknown_session",
                        "session/update pour une session inconnue",
                    )];
                }
                let text = match (
                    params
                        .pointer("/update/content/type")
                        .and_then(Value::as_str),
                    params
                        .pointer("/update/content/text")
                        .and_then(Value::as_str),
                ) {
                    (Some("text"), Some(t)) => Value::String(t.to_string()),
                    _ => Value::Null,
                };
                vec![json!({"type": "update", "session_id": sid, "kind": kind, "text": text})]
            }
            ("session/request_permission", Some(id)) => self.in_permission(id, params),
            (_, Some(id)) => {
                self.pending.push(Pending {
                    key: id.to_string(),
                    request_id: id.clone(),
                    options: None,
                });
                vec![json!({"type": "unsupported_request", "method": method, "request_id": id})]
            }
            (_, None) => vec![json!({"type": "ignored_notification", "method": method})],
        }
    }

    fn in_permission(&mut self, id: &Value, params: &Value) -> Vec<Value> {
        let Some(sid) = str_param(params, "sessionId") else {
            return vec![err(
                "invalid_message",
                "session/request_permission : sessionId requis",
            )];
        };
        let Some(tool_call_id) = params
            .pointer("/toolCall/toolCallId")
            .and_then(Value::as_str)
        else {
            return vec![err(
                "invalid_message",
                "session/request_permission : toolCall.toolCallId requis",
            )];
        };
        let Some(raw) = params
            .get("options")
            .and_then(Value::as_array)
            .filter(|a| !a.is_empty())
        else {
            return vec![err(
                "invalid_message",
                "session/request_permission : options non vide requis",
            )];
        };
        let mut options = Vec::new();
        let mut shown = Vec::new();
        for o in raw {
            let (Some(oid), Some(name), Some(kind)) = (
                str_param(o, "optionId"),
                str_param(o, "name"),
                str_param(o, "kind"),
            ) else {
                return vec![err(
                    "invalid_message",
                    "option : optionId, name et kind requis",
                )];
            };
            if !OPTION_KINDS.contains(&kind) {
                return vec![err("invalid_message", "option : kind inconnu")];
            }
            options.push((oid.to_string(), kind.to_string()));
            shown.push(json!({"option_id": oid, "name": name, "kind": kind}));
        }
        if !self.known_session(sid) {
            return vec![err(
                "unknown_session",
                "permission pour une session inconnue",
            )];
        }
        let key = id.to_string();
        if self.pending.iter().any(|p| p.key == key) {
            return vec![err("duplicate_request", "id de requete deja en attente")];
        }
        self.pending.push(Pending {
            key,
            request_id: id.clone(),
            options: Some(options),
        });
        vec![json!({
            "type": "permission_requested",
            "request_id": id,
            "session_id": sid,
            "tool_call_id": tool_call_id,
            "options": shown,
        })]
    }

    fn agent_error(method: &str, error: &Value) -> Value {
        let mut m = Map::new();
        m.insert("type".into(), json!("error"));
        m.insert("code".into(), json!("agent_error"));
        m.insert(
            "message".into(),
            json!(error.get("message").and_then(Value::as_str).unwrap_or("")),
        );
        m.insert("method".into(), json!(method));
        m.insert(
            "rpc_code".into(),
            error
                .get("code")
                .filter(|c| c.is_i64())
                .cloned()
                .unwrap_or(Value::Null),
        );
        Value::Object(m)
    }

    fn in_response(&mut self, msg: &Value) -> Vec<Value> {
        let key = msg["id"].to_string();
        let Some(pos) = self.outstanding.iter().position(|o| o.key == key) else {
            return vec![err(
                "unexpected_response",
                "reponse sans requete en attente",
            )];
        };
        let method = self.outstanding.remove(pos).method;
        let resting = if self.session_id.is_some() {
            State::Idle
        } else {
            State::Ready
        };
        if let Some(error) = msg.get("error") {
            self.state = if method == "initialize" {
                State::Closed
            } else {
                resting
            };
            if method == "session/load" {
                self.opening_session = None;
            }
            return vec![Self::agent_error(&method, error)];
        }
        let null = Value::Null;
        let result = msg.get("result").unwrap_or(&null);
        match method.as_str() {
            "initialize" => {
                let Some(version) = result
                    .get("protocolVersion")
                    .filter(|v| v.is_i64() || v.is_u64())
                else {
                    self.state = State::Closed;
                    return vec![err(
                        "invalid_message",
                        "initialize : protocolVersion absent de la reponse",
                    )];
                };
                self.load_session = result
                    .pointer("/agentCapabilities/loadSession")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                let auth = result
                    .get("authMethods")
                    .and_then(Value::as_array)
                    .map_or(0, Vec::len);
                self.state = State::Ready;
                vec![json!({
                    "type": "initialized",
                    "protocol_version": version,
                    "load_session": self.load_session,
                    "auth_methods": auth,
                })]
            }
            "session/new" => match result.get("sessionId").and_then(Value::as_str) {
                Some(sid) => {
                    self.session_id = Some(sid.to_string());
                    self.state = State::Idle;
                    vec![json!({"type": "session_opened", "session_id": sid, "loaded": false})]
                }
                None => {
                    self.state = resting;
                    vec![err(
                        "invalid_message",
                        "session/new : sessionId absent de la reponse",
                    )]
                }
            },
            "session/load" => {
                self.session_id = self.opening_session.take();
                self.state = State::Idle;
                vec![
                    json!({"type": "session_opened", "session_id": self.session_id, "loaded": true}),
                ]
            }
            _ => {
                // session/prompt
                self.state = State::Idle;
                match result.get("stopReason").and_then(Value::as_str) {
                    Some(r) if STOP_REASONS.contains(&r) => {
                        vec![
                            json!({"type": "turn_ended", "session_id": self.session_id, "stop_reason": r}),
                        ]
                    }
                    _ => vec![err(
                        "invalid_message",
                        "session/prompt : stopReason inconnu ou absent",
                    )],
                }
            }
        }
    }

    /// Ferme la machine (sous-processus termine ou arrete par l'appelant).
    pub fn close(&mut self) -> Vec<Value> {
        self.state = State::Closed;
        self.outstanding.clear();
        vec![json!({"type": "closed"})]
    }
}

/// Rejoue une transcription `[{"dir": "out"|"in"|"close", "msg": {...}}, ...]`.
///
/// Rend `{"steps": [{"events": [...], "state": "..."}], "final": {...}}`.
pub fn replay(transcript: &Value) -> Result<Value, String> {
    let entries = transcript
        .as_array()
        .ok_or("la transcription doit etre une liste")?;
    let mut machine = Machine::new();
    let mut steps = Vec::new();
    for (i, entry) in entries.iter().enumerate() {
        let events = match entry.get("dir").and_then(Value::as_str) {
            Some("out") => machine.on_outgoing(entry.get("msg").unwrap_or(&Value::Null)),
            Some("in") => machine.on_incoming(entry.get("msg").unwrap_or(&Value::Null)),
            Some("close") => machine.close(),
            _ => return Err(format!("entree {i} : dir doit valoir out, in ou close")),
        };
        steps.push(json!({"events": events, "state": machine.state().as_str()}));
    }
    Ok(json!({"steps": steps, "final": machine.snapshot()}))
}

#[cfg(feature = "extension-module")]
mod python {
    use super::{replay, Machine, ABI_VERSION};
    use pyo3::exceptions::PyValueError;
    use pyo3::prelude::*;
    use serde_json::Value;

    fn parse(raw: &str) -> PyResult<Value> {
        serde_json::from_str(raw).map_err(|e| PyValueError::new_err(e.to_string()))
    }

    fn dump(v: &Value) -> String {
        v.to_string()
    }

    /// Machine d'etats ; les messages et les evenements circulent en JSON
    /// texte pour que la frontiere reste etroite.
    #[pyclass(name = "Machine")]
    struct PyMachine {
        inner: Machine,
    }

    #[pymethods]
    impl PyMachine {
        #[new]
        fn new() -> Self {
            PyMachine {
                inner: Machine::new(),
            }
        }

        fn on_outgoing(&mut self, msg_json: &str) -> PyResult<String> {
            Ok(dump(&Value::Array(
                self.inner.on_outgoing(&parse(msg_json)?),
            )))
        }

        fn on_incoming(&mut self, msg_json: &str) -> PyResult<String> {
            Ok(dump(&Value::Array(
                self.inner.on_incoming(&parse(msg_json)?),
            )))
        }

        fn close(&mut self) -> String {
            dump(&Value::Array(self.inner.close()))
        }

        fn snapshot(&self) -> String {
            dump(&self.inner.snapshot())
        }
    }

    #[pyfunction(name = "replay")]
    fn py_replay(transcript_json: &str) -> PyResult<String> {
        replay(&parse(transcript_json)?)
            .map(|v| dump(&v))
            .map_err(PyValueError::new_err)
    }

    #[pymodule]
    fn grimoire_acp_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_class::<PyMachine>()?;
        m.add_function(wrap_pyfunction!(py_replay, m)?)?;
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        m.add("ABI_VERSION", ABI_VERSION)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn fixtures() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures/acp")
    }

    fn read(name: &str) -> Value {
        let raw = std::fs::read_to_string(fixtures().join(name)).expect(name);
        serde_json::from_str(&raw).expect(name)
    }

    /// Chaque `X.transcript.json` rejoue exactement `X.expected.json` :
    /// le meme fichier que lit le test Python.
    #[test]
    fn every_transcript_matches_its_golden() {
        let mut seen = 0;
        for entry in std::fs::read_dir(fixtures()).expect("fixtures/acp") {
            let name = entry.unwrap().file_name().into_string().unwrap();
            let Some(stem) = name.strip_suffix(".transcript.json") else {
                continue;
            };
            let got = replay(&read(&name)).unwrap();
            assert_eq!(got, read(&format!("{stem}.expected.json")), "{stem}");
            seen += 1;
        }
        assert!(seen >= 6, "transcriptions attendues, trouve {seen}");
    }

    fn out(m: &mut Machine, v: Value) -> Vec<Value> {
        m.on_outgoing(&v)
    }

    fn ready_with_session(load: bool) -> Machine {
        let mut m = Machine::new();
        out(
            &mut m,
            json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1}}),
        );
        m.on_incoming(&json!({"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1,"agentCapabilities":{"loadSession":load}}}));
        out(
            &mut m,
            json!({"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/p"}}),
        );
        m.on_incoming(&json!({"jsonrpc":"2.0","id":2,"result":{"sessionId":"s1"}}));
        m
    }

    #[test]
    fn prompt_before_session_is_refused_without_state_change() {
        let mut m = Machine::new();
        let ev = out(
            &mut m,
            json!({"jsonrpc":"2.0","id":1,"method":"session/prompt","params":{"sessionId":"s","prompt":[]}}),
        );
        assert_eq!(ev[0]["code"], "no_session");
        assert_eq!(m.state(), State::New);
    }

    #[test]
    fn load_is_refused_when_agent_does_not_announce_it() {
        let mut m = ready_with_session(false);
        let ev = out(
            &mut m,
            json!({"jsonrpc":"2.0","id":3,"method":"session/load","params":{"sessionId":"s1","cwd":"/p"}}),
        );
        assert_eq!(ev[0]["code"], "load_unsupported");
        assert_eq!(m.state(), State::Idle);
    }

    #[test]
    fn permission_with_unknown_option_stays_pending() {
        let mut m = ready_with_session(false);
        out(
            &mut m,
            json!({"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{"sessionId":"s1","prompt":[]}}),
        );
        m.on_incoming(
            &json!({"jsonrpc":"2.0","id":"p","method":"session/request_permission","params":{
            "sessionId":"s1","toolCall":{"toolCallId":"t"},
            "options":[{"optionId":"a","name":"Oui","kind":"allow_once"}]}}),
        );
        let ev = out(
            &mut m,
            json!({"jsonrpc":"2.0","id":"p","result":{"outcome":{"outcome":"selected","optionId":"zzz"}}}),
        );
        assert_eq!(ev[0]["code"], "invalid_permission_response");
        assert_eq!(m.snapshot()["pending_permissions"], json!(["p"]));
    }

    #[test]
    fn garbage_never_panics() {
        let mut m = Machine::new();
        for v in [
            json!(null),
            json!(3),
            json!([]),
            json!({}),
            json!({"jsonrpc":"2.0","id":1.5,"method":"x"}),
        ] {
            assert_eq!(m.on_incoming(&v)[0]["type"], "error");
            assert_eq!(m.on_outgoing(&v)[0]["type"], "error");
        }
    }
}
