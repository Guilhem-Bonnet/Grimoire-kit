//! Coeur Rust optionnel de la machine a etats de `grimoire.flows`.
//!
//! Quatrieme port Rust du kit (issue #354 de Guilhem-Bonnet/Grimoire-kit,
//! apres `rust/grimoire-policies-core/`, `rust/grimoire-schema-core/` et
//! `rust/grimoire-hosts-core/`, meme montage). Ce crate porte les parties
//! **pures** de trois modules :
//!
//! - `grimoire.flows.engine` (`check_output_against_contract`,
//!   `FlowEngine._current_node`, la precondition et le calcul de
//!   `ResumeOutcome` dans `FlowEngine.resume`, le decoupage
//!   completed/pending dans `FlowEngine.status`).
//! - `grimoire.runtime.kernel` (la table `_WF_TRANSITIONS` et la
//!   precondition de `advance_step`).
//! - `grimoire.runtime.schemas` (`WorkflowStatus`, comme enum Rust
//!   exhaustive plutot que la `StrEnum` Python).
//!
//! Laisse en Python, deliberement : la persistance (`_save_meta`/
//! `_load_meta`, `instances.jsonl`/`run_events.jsonl`/`checkpoints.jsonl`),
//! l'exécuteur (`NodeExecutor`), le contexte (`ExecutionContext`), la
//! lecture/validation de blueprint (`blueprint_loader.py`, JSON + Kahn), et
//! toute E/S disque. Ce module ne fait aucune E/S et ne connait aucun
//! fichier.
//!
//! ## La propriete que ce port doit demontrer
//!
//! `WorkflowStatus` ci-dessous miroite la `StrEnum` Python de
//! `grimoire.runtime.schemas`. Chaque fonction qui la consomme
//! (`is_flow_terminal`, `allowed_transitions`, `current_node_core`, ...) est
//! un `match` exhaustif, sans branche `_ =>` : si une variante est ajoutee
//! cote Python sans etre reportee ici, ce crate ne compile plus. Cote
//! Python, une valeur de `StrEnum` non geree dans une comparaison ne casse
//! rien a la compilation — elle se contente de rendre une comparaison
//! `False` a l'execution, potentiellement au milieu d'une reprise de run.
//! C'est exactement ce qui s'est produit ici (section suivante).
//!
//! ## Ce que le compilateur/l'oracle Rust a trouve
//!
//! `FlowEngine._TERMINAL_STATUSES` (Python, `engine.py`) valait
//! `(COMPLETED, VERIFIED, ABORTED)` — `REFUSED` en etait absent, alors que
//! `RuntimeKernel` le traite deja comme terminal (aucune transition sortante
//! dans `_WF_TRANSITIONS`, ecrit par `mediate_tool` quand les plafonds MAST
//! (B11) sont atteints). Verifie empiriquement avant correctif : sur un run
//! REFUSED, `FlowEngine.resume()` levait bien une `GrimoireRuntimeError`,
//! mais celle du message generique de `RuntimeKernel.advance_step`
//! (« advance_step requires RUNNING, CHECKPOINTED or BLOCKED, got refused
//! for WFI-... ») au lieu du message nomme de l'ancien garde-fou de
//! `resume()` (« run RUN-... est refused, rien à reprendre ») — et
//! `FlowEngine.status()` renvoyait un `current_node` perime (le dernier
//! node demarre avant le refus) avec des `pending_nodes` non vides, comme
//! si le run continuait alors qu'il est mort. Aucune corruption de donnees
//! (le refus lui-meme, cote kernel, est deja correct et definitif), mais un
//! hote lisant `status()` apres un refus voit un run qui semble progresser.
//! Corrige dans cette PR (trivial, sans risque : `REFUSED` ajoute a
//! `_TERMINAL_STATUSES`, aucun appelant existant ne dependait du
//! comportement precedent — voir
//! `tests/unit/test_flows_rust_parity.py::test_refused_run_is_terminal_to_resume_and_status`).
//! Le coeur Rust n'a jamais eu ce trou : `is_flow_terminal` est un `match`
//! exhaustif sur les 9 variantes, ecrire un statut terminal sans le
//! marquer `true` quelque part ne compile pas silencieusement — c'est
//! exactement le role attendu de ce port (cf. commit du port #392, #412).
//!
//! ## Corrige : un abandon avant tout progres n'affiche plus tous les nodes comme faits
//!
//! `FlowEngine.status()` derivait `completed_nodes` de la seule position de
//! `current_node` dans l'ordre topologique (`order[:idx]`), jamais des
//! `completed_steps` reellement checkpointes. Sur un run ABORTED/REFUSED
//! avant tout progres, `current_node` devient `None` (terminal) exactement
//! comme sur un run termine avec succes — `idx = len(order)` traitait donc
//! les deux cas de facon identique, rendant `completed_nodes == order` en
//! entier meme quand *aucun* node n'a reellement ete complete (issue #414,
//! trouvee en portant ce decoupage vers Rust — voir PR #413). Corrige des
//! deux cotes en meme temps, meme verdict : `status_slices_core` ci-dessous
//! ne retombe sur `order` en entier que pour un succes (`COMPLETED`/
//! `VERIFIED`, ou `current_node` est bien resolu dans `order`) ; pour un
//! run mort en cours de route (`ABORTED`/`REFUSED`) sans node courant
//! resoluble, la source de verite devient les `completed_steps` du dernier
//! checkpoint connu du `RuntimeKernel` (liste vide si aucun checkpoint
//! n'existe encore — le run est mort avant le premier `resume()`).
//! `pending_nodes` reste vide par convention sur un run mort : on ne sait
//! pas si l'hote comptait reprendre. Voir
//! `tests/unit/test_flows_rust_parity.py::test_aborted_run_status_slicing_reflects_real_progress`.
//!
//! Le statut `PAUSED` est declare dans `_WF_TRANSITIONS` (Python) mais
//! aucune methode de `RuntimeKernel` ne l'atteint jamais — mort cote
//! emission, vivant cote table de transition. `current_node_core`
//! ci-dessous doit neanmoins statuer dessus (`match` exhaustif oblige) :
//! il suit la meme branche que RUNNING/CREATED, comme le fait deja
//! implicitement le `if`/`elif`/else de `_current_node` cote Python (ni
//! BLOCKED ni CHECKPOINTED -> le else générique). Documente ici, jamais
//! rendu explicite cote Python avant ce port.
//!
//! Un statut inconnu dans des metadonnees persistees a la main
//! (`instances.jsonl` edite manuellement) fait deja lever `WorkflowStatus(d
//! ["status"])` une `ValueError` non rattrapee cote Python
//! (`WorkflowInstance.from_dict`) — jamais un comportement par defaut
//! silencieux. `WorkflowStatus::parse` ci-dessous fait de meme (`Err`,
//! jamais une variante par defaut) : les deux backends refusent, aucun des
//! deux ne choisit un statut au hasard. Documente, pas un defaut a
//! corriger (voir
//! `tests/unit/test_flows_rust_parity.py::test_unknown_status_string_rejected_by_both_backends`).

#![cfg_attr(not(feature = "extension-module"), allow(dead_code))]

// ── Representation dynamique minimale ───────────────────────────────────────
//
// Meme philosophie que rust/grimoire-hosts-core/ et rust/grimoire-schema-core/ :
// une representation suffisante pour porter une valeur JSON/Python arbitraire
// (la sortie `output: dict[str, Any]` soumise par l'hote a `resume()`,
// potentiellement malformee), convertible dans les deux sens a la frontiere
// PyO3. Contrairement a `grimoire-hosts-core`, aucun parseur YAML n'est
// necessaire ici : `output` est deja un dict Python analyse en amont (JSON ou
// litteral CLI), jamais du texte brut lu par ce crate.
#[derive(Debug, Clone, PartialEq)]
enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    List(Vec<Value>),
    /// Paires (cle, valeur) dans l'ordre d'insertion, comme un dict Python
    /// (3.7+).
    Map(Vec<(String, Value)>),
}

impl Value {
    fn as_map(&self) -> Option<&[(String, Value)]> {
        match self {
            Value::Map(entries) => Some(entries),
            _ => None,
        }
    }

    fn get<'a>(&'a self, key: &str) -> Option<&'a Value> {
        self.as_map()?
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v)
    }

    fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(s) => Some(s.as_str()),
            _ => None,
        }
    }

    /// Approxime `repr()` de Python pour les types scalaires qu'un contrat
    /// de pin malforme peut produire (chaine, `None`, booleen, nombre).
    /// Utilise uniquement pour reproduire a l'identique le message d'erreur
    /// de `check_output_against_contract` (`{produced!r}`) — jamais pour une
    /// decision logique.
    fn py_repr(&self) -> String {
        match self {
            Value::Null => "None".to_string(),
            Value::Bool(true) => "True".to_string(),
            Value::Bool(false) => "False".to_string(),
            Value::Int(i) => i.to_string(),
            Value::Float(f) => {
                if f.fract() == 0.0 && f.is_finite() {
                    format!("{f:.1}")
                } else {
                    format!("{f}")
                }
            }
            Value::Str(s) => {
                let escaped = s.replace('\\', "\\\\").replace('\'', "\\'");
                format!("'{escaped}'")
            }
            Value::List(items) => {
                let inner: Vec<String> = items.iter().map(Value::py_repr).collect();
                format!("[{}]", inner.join(", "))
            }
            Value::Map(entries) => {
                let inner: Vec<String> = entries
                    .iter()
                    .map(|(k, v)| format!("'{}': {}", k.replace('\'', "\\'"), v.py_repr()))
                    .collect();
                format!("{{{}}}", inner.join(", "))
            }
        }
    }
}

// ── WorkflowStatus : miroir de grimoire.runtime.schemas.WorkflowStatus ─────

/// Les 9 valeurs de `WorkflowStatus` (`grimoire.runtime.schemas`). Toute
/// fonction de ce fichier qui en depend est un `match` exhaustif : ajouter
/// une variante ici sans mettre a jour `is_flow_terminal`,
/// `allowed_transitions` et `current_node_core` empeche la compilation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum WorkflowStatus {
    Created,
    Running,
    Checkpointed,
    Paused,
    Blocked,
    Aborted,
    Refused,
    Completed,
    Verified,
}

impl WorkflowStatus {
    /// Miroir de `WorkflowStatus(value)` cote Python : `Err` — jamais une
    /// variante par defaut — sur une valeur non reconnue. Un statut inconnu
    /// dans des metadonnees persistees a la main doit rester un refus
    /// explicite des deux cotes de la frontiere (voir le docstring de ce
    /// module).
    fn parse(value: &str) -> Result<Self, String> {
        Ok(match value {
            "created" => WorkflowStatus::Created,
            "running" => WorkflowStatus::Running,
            "checkpointed" => WorkflowStatus::Checkpointed,
            "paused" => WorkflowStatus::Paused,
            "blocked" => WorkflowStatus::Blocked,
            "aborted" => WorkflowStatus::Aborted,
            "refused" => WorkflowStatus::Refused,
            "completed" => WorkflowStatus::Completed,
            "verified" => WorkflowStatus::Verified,
            other => {
                return Err(format!(
                    "{other:?} is not a valid WorkflowStatus (attendu: created, running, \
                     checkpointed, paused, blocked, aborted, refused, completed, verified)"
                ))
            }
        })
    }

    fn as_str(&self) -> &'static str {
        match self {
            WorkflowStatus::Created => "created",
            WorkflowStatus::Running => "running",
            WorkflowStatus::Checkpointed => "checkpointed",
            WorkflowStatus::Paused => "paused",
            WorkflowStatus::Blocked => "blocked",
            WorkflowStatus::Aborted => "aborted",
            WorkflowStatus::Refused => "refused",
            WorkflowStatus::Completed => "completed",
            WorkflowStatus::Verified => "verified",
        }
    }
}

/// Miroir **corrige** de `FlowEngine._TERMINAL_STATUSES`
/// (`grimoire/flows/engine.py`) : un run dans un de ces statuts n'a plus
/// rien a reprendre. `REFUSED` y est inclus (voir le docstring de ce
/// module pour le defaut Python que cela corrige) ; `COMPLETED` y reste
/// bien qu'il porte une transition kernel sortante vers `VERIFIED`
/// (`_WF_TRANSITIONS`) — `FlowEngine` ne verifie jamais un run lui-meme,
/// donc `COMPLETED` est deja un mur du point de vue du moteur de flows,
/// meme si `RuntimeKernel.complete()` -> `RuntimeKernel` seul sait encore
/// transitionner vers `VERIFIED`. `match` exhaustif : une 10e variante de
/// statut qui n'est ni ajoutee ici ni exclue explicitement ne compile pas.
fn is_flow_terminal(status: WorkflowStatus) -> bool {
    match status {
        WorkflowStatus::Completed
        | WorkflowStatus::Verified
        | WorkflowStatus::Aborted
        | WorkflowStatus::Refused => true,
        WorkflowStatus::Created
        | WorkflowStatus::Running
        | WorkflowStatus::Checkpointed
        | WorkflowStatus::Paused
        | WorkflowStatus::Blocked => false,
    }
}

/// Miroir de `_WF_TRANSITIONS` (`grimoire/runtime/kernel.py`). `match`
/// exhaustif sur le statut de depart : chaque bras liste les statuts
/// d'arrivee legaux, exactement comme le dict Python — mais une variante
/// de statut ajoutee sans bras correspondant ici ne compile pas, alors que
/// le dict Python `.get(wfi.status, frozenset())` retomberait
/// silencieusement sur "aucune transition legale" pour toute cle absente.
fn allowed_transitions(status: WorkflowStatus) -> &'static [WorkflowStatus] {
    use WorkflowStatus::*;
    match status {
        Created => &[Running, Aborted, Refused],
        Running => &[Checkpointed, Paused, Blocked, Completed, Aborted, Refused],
        Checkpointed => &[Running, Aborted, Refused],
        Paused => &[Running, Aborted, Refused],
        Blocked => &[Running, Aborted, Refused],
        Completed => &[Verified],
        Verified => &[],
        Aborted => &[],
        Refused => &[],
    }
}

/// Miroir de `RuntimeKernel._transition` : la transition `from -> to`
/// est-elle legale ?
fn can_transition(from: WorkflowStatus, to: WorkflowStatus) -> bool {
    allowed_transitions(from).contains(&to)
}

/// Miroir de la precondition de `RuntimeKernel.advance_step` : `Ok(true)`
/// si une transition explicite vers RUNNING est necessaire (depuis
/// CHECKPOINTED ou BLOCKED), `Ok(false)` si le statut est deja RUNNING (le
/// tout premier node d'un run, juste apres `start()`), `Err(())` pour tout
/// autre statut de depart (CREATED, PAUSED, COMPLETED, ABORTED, REFUSED,
/// VERIFIED) — le message d'erreur exact (qui nomme `wfi_id`) est compose
/// cote appelant, ce module ne connait pas d'identifiant de run.
fn advance_step_decision(status: WorkflowStatus) -> Result<bool, ()> {
    match status {
        WorkflowStatus::Checkpointed | WorkflowStatus::Blocked => Ok(true),
        WorkflowStatus::Running => Ok(false),
        WorkflowStatus::Created
        | WorkflowStatus::Paused
        | WorkflowStatus::Completed
        | WorkflowStatus::Aborted
        | WorkflowStatus::Refused
        | WorkflowStatus::Verified => Err(()),
    }
}

// ── check_output_against_contract ───────────────────────────────────────────

/// Miroir de `grimoire.flows.engine.check_output_against_contract`.
/// `outputs` est la liste `(pin_id, contract)` des pins de sortie du node
/// (`NodeContract.outputs`, deja derivees du blueprint cote Python — ce
/// module ne lit aucun blueprint). Memes messages, mot pour mot, que la
/// reference Python (`{produced!r}` reproduit par `Value::py_repr`).
fn check_output_against_contract_core(
    node_id: &str,
    outputs: &[(String, String)],
    output: &Value,
) -> Vec<String> {
    let pins_out = match output.get("pins").and_then(Value::as_map) {
        Some(entries) => entries,
        None => {
            return vec![format!(
                "node={node_id} : sortie sans objet 'pins' (forme attendue : {{'pins': {{...}}}})"
            )]
        }
    };
    let mut faults = Vec::new();
    for (pin_id, expected_contract) in outputs {
        let entry = pins_out.iter().find(|(k, _)| k == pin_id).map(|(_, v)| v);
        let entry_map = match entry.and_then(Value::as_map) {
            Some(m) => m,
            None => {
                faults.push(format!(
                    "node={node_id} pin={pin_id} : absente de la sortie soumise"
                ));
                continue;
            }
        };
        let produced = entry_map
            .iter()
            .find(|(k, _)| k == "contract")
            .map(|(_, v)| v);
        let matches = produced.and_then(Value::as_str) == Some(expected_contract.as_str());
        if !matches {
            let produced_repr = produced
                .map(Value::py_repr)
                .unwrap_or_else(|| "None".to_string());
            faults.push(format!(
                "node={node_id} pin={pin_id} : contrat produit {produced_repr} != attendu '{expected_contract}'"
            ));
        }
    }
    faults
}

// ── current_node ─────────────────────────────────────────────────────────

/// Ce que sait `FlowEngine._current_node` sur l'historique du run, une fois
/// les journaux d'evenements et de checkpoints deja lus cote Python (E/S
/// laissee cote appelant — ce module ne lit aucun fichier). `NoCheckpoints`
/// distingue "aucun checkpoint n'existe encore" de "un checkpoint existe et
/// sa liste de pending est vide" (`PendingHead(None)`) — une ambiguite que
/// `Option<Option<_>>` rendrait invisible a la frontiere PyO3.
enum CheckpointPendingHead {
    NoCheckpoints,
    PendingHead(Option<String>),
}

/// Miroir de `FlowEngine._current_node`. `order` est l'ordre topologique du
/// run (`FlowRunMeta.order`) ; `last_step_failed` est le `step_id` du
/// dernier evenement `STEP_FAILED` (le plus recent d'abord, cote Python) ;
/// `last_step_started` celui du dernier `STEP_STARTED`.
fn current_node_core(
    status: WorkflowStatus,
    order: &[String],
    last_step_failed: Option<&str>,
    checkpoint_pending_head: CheckpointPendingHead,
    last_step_started: Option<&str>,
) -> Option<String> {
    if is_flow_terminal(status) {
        return None;
    }
    match status {
        WorkflowStatus::Blocked => last_step_failed
            .map(str::to_string)
            .or_else(|| order.first().cloned()),
        WorkflowStatus::Checkpointed => match checkpoint_pending_head {
            CheckpointPendingHead::NoCheckpoints => order.first().cloned(),
            CheckpointPendingHead::PendingHead(head) => head,
        },
        // RUNNING, CREATED ou PAUSED (mort cote emission, voir le docstring
        // de ce module) : meme branche que la reference Python — dernier
        // node demarre, sinon le premier de l'ordre.
        WorkflowStatus::Running | WorkflowStatus::Created | WorkflowStatus::Paused => {
            last_step_started
                .map(str::to_string)
                .or_else(|| order.first().cloned())
        }
        WorkflowStatus::Completed
        | WorkflowStatus::Verified
        | WorkflowStatus::Aborted
        | WorkflowStatus::Refused => {
            unreachable!("is_flow_terminal a deja intercepte ces statuts")
        }
    }
}

// ── resume(): precondition, puis calcul de ResumeOutcome ───────────────────

/// Miroir de la garde en tete de `FlowEngine.resume` : `false` quand le run
/// est deja termine du point de vue du moteur de flows (voir
/// `is_flow_terminal`) — plus rien a reprendre. Le message exact (qui nomme
/// `run_id`) est compose cote appelant.
fn resume_allowed(status: WorkflowStatus) -> bool {
    !is_flow_terminal(status)
}

struct ResumeOutcomeCore {
    ok: bool,
    finished: bool,
    node_id: Option<String>,
    faults: Vec<String>,
}

/// Miroir du corps de `FlowEngine.resume` une fois `check_output_against_contract`
/// evalue : `faults` non vide -> suspendu sur `current_id` ; `faults` vide et
/// aucun node restant apres `current_id` -> termine ; sinon -> avance sur le
/// node suivant. `None` si `current_id` n'apparait pas dans `order` — un etat
/// que la reference Python ne peut pas representer explicitement
/// (`order.index` y leverait un `ValueError` non nomme au lieu d'un refus
/// clair ; voir `tests/unit/test_flows_rust_parity.py`).
fn compute_resume_outcome(
    order: &[String],
    current_id: &str,
    faults: Vec<String>,
) -> Option<ResumeOutcomeCore> {
    if !faults.is_empty() {
        return Some(ResumeOutcomeCore {
            ok: false,
            finished: false,
            node_id: Some(current_id.to_string()),
            faults,
        });
    }
    let idx = order.iter().position(|n| n == current_id)?;
    let pending = &order[idx + 1..];
    if pending.is_empty() {
        return Some(ResumeOutcomeCore {
            ok: true,
            finished: true,
            node_id: Some(current_id.to_string()),
            faults: Vec::new(),
        });
    }
    Some(ResumeOutcomeCore {
        ok: true,
        finished: false,
        node_id: Some(pending[0].clone()),
        faults: Vec::new(),
    })
}

// ── status(): decoupage completed/pending ──────────────────────────────────

/// Decoupage completed/pending de `FlowEngine.status` (issue #414, voir le
/// docstring de ce module). Quand `current_id` est resolu dans `order`
/// (run en cours), decoupage positionnel inchange : `order[:idx]` /
/// `order[idx+1:]`. Sinon (`current_id` absent — run termine, ou terminal
/// sans node courant resoluble) : `COMPLETED`/`VERIFIED` rendent `order` en
/// entier (succes reel, tout est fait) ; tout le reste (`ABORTED`,
/// `REFUSED`, ou tout autre statut degenere sans node courant) rend les
/// `completed_steps` du dernier checkpoint connu — vide si aucun checkpoint
/// n'existe encore, jamais une invention deduite de la position. Un
/// `current_id` non resoluble dans `order` (ne devrait jamais arriver,
/// order et current_id venant du meme run) retombe defensivement sur
/// `order` en entier, comme avant.
fn status_slices_core(
    order: &[String],
    current_id: Option<&str>,
    status: WorkflowStatus,
    checkpoint_completed: &[String],
) -> (Vec<String>, Vec<String>) {
    if let Some(id) = current_id {
        return match order.iter().position(|n| n == id) {
            Some(idx) => (order[..idx].to_vec(), order[idx + 1..].to_vec()),
            None => (order.to_vec(), Vec::new()),
        };
    }
    match status {
        WorkflowStatus::Completed | WorkflowStatus::Verified => (order.to_vec(), Vec::new()),
        _ => (checkpoint_completed.to_vec(), Vec::new()),
    }
}

// ── Frontiere PyO3 ───────────────────────────────────────────────────────

#[cfg(feature = "extension-module")]
mod py_bridge {
    use super::{
        advance_step_decision, allowed_transitions, can_transition,
        check_output_against_contract_core, compute_resume_outcome, current_node_core,
        is_flow_terminal, resume_allowed, status_slices_core, CheckpointPendingHead, Value,
        WorkflowStatus,
    };
    use pyo3::exceptions::{PyTypeError, PyValueError};
    use pyo3::prelude::*;
    use pyo3::types::{PyBool, PyDict, PyList, PyString, PyTuple};

    /// Convertit une valeur Python arbitraire (la sortie `output` soumise a
    /// `resume()`, potentiellement malformee) vers la representation
    /// dynamique de ce crate. Jamais d'echec : la forme malformee doit
    /// rester visible pour que `check_output_against_contract_core` la
    /// traite exactement comme le fait deja son equivalent Python (un test
    /// d'`isinstance`), pas etre rejetee des la frontiere.
    fn to_value(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
        if obj.is_none() {
            return Ok(Value::Null);
        }
        // PyBool avant PyInt : `bool` est une sous-classe de `int` cote
        // Python (`isinstance(True, int)` est vrai).
        if let Ok(b) = obj.cast::<PyBool>() {
            return Ok(Value::Bool(b.is_true()));
        }
        if let Ok(d) = obj.cast::<PyDict>() {
            let mut entries = Vec::with_capacity(d.len());
            for (k, v) in d.iter() {
                entries.push((k.str()?.to_string(), to_value(&v)?));
            }
            return Ok(Value::Map(entries));
        }
        if let Ok(l) = obj.cast::<PyList>() {
            let mut items = Vec::with_capacity(l.len());
            for item in l.iter() {
                items.push(to_value(&item)?);
            }
            return Ok(Value::List(items));
        }
        if let Ok(t) = obj.cast::<PyTuple>() {
            let mut items = Vec::with_capacity(t.len());
            for item in t.iter() {
                items.push(to_value(&item)?);
            }
            return Ok(Value::List(items));
        }
        if let Ok(st) = obj.cast::<PyString>() {
            return Ok(Value::Str(st.to_string()));
        }
        if let Ok(i) = obj.extract::<i64>() {
            return Ok(Value::Int(i));
        }
        if let Ok(f) = obj.extract::<f64>() {
            return Ok(Value::Float(f));
        }
        Ok(Value::Str(obj.str()?.to_string()))
    }

    fn value_from_any(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
        to_value(obj).map_err(|e: PyErr| {
            PyTypeError::new_err(format!("grimoire_flows_core: conversion impossible: {e}"))
        })
    }

    fn parse_status(raw: &str) -> PyResult<WorkflowStatus> {
        WorkflowStatus::parse(raw).map_err(PyValueError::new_err)
    }

    /// Frontiere PyO3 pour `WorkflowStatus(value)` — expose surtout pour
    /// les tests de parite (`test_unknown_status_string_rejected_by_both_backends`).
    /// `Err` (jamais une variante par defaut) sur une valeur non reconnue,
    /// meme verdict que la `StrEnum` Python.
    #[pyfunction]
    fn parse_workflow_status(raw: &str) -> PyResult<String> {
        Ok(parse_status(raw)?.as_str().to_string())
    }

    /// Frontiere PyO3 pour la version corrigee de
    /// `FlowEngine._TERMINAL_STATUSES` (voir le docstring du module).
    #[pyfunction]
    fn is_flow_terminal_status(status: &str) -> PyResult<bool> {
        Ok(is_flow_terminal(parse_status(status)?))
    }

    /// Frontiere PyO3 pour `RuntimeKernel._WF_TRANSITIONS[from]`.
    #[pyfunction]
    fn allowed_transitions_from(status: &str) -> PyResult<Vec<String>> {
        Ok(allowed_transitions(parse_status(status)?)
            .iter()
            .map(|s| s.as_str().to_string())
            .collect())
    }

    /// Frontiere PyO3 pour `RuntimeKernel._transition` (test de legalite
    /// seul — la mutation et l'emission d'evenement restent cote Python).
    #[pyfunction]
    fn can_transition_status(from_status: &str, to_status: &str) -> PyResult<bool> {
        Ok(can_transition(
            parse_status(from_status)?,
            parse_status(to_status)?,
        ))
    }

    /// Frontiere PyO3 pour la precondition de `RuntimeKernel.advance_step`.
    /// Retourne `True` si une transition explicite vers RUNNING est requise
    /// (CHECKPOINTED/BLOCKED), `False` si deja RUNNING ; leve
    /// `GrimoireRuntimeError`-compatible (`ValueError` ici, le module Python
    /// appelant l'enveloppe) pour tout autre statut de depart.
    #[pyfunction]
    fn advance_step_needs_transition(status: &str, wfi_id: &str) -> PyResult<bool> {
        let parsed = parse_status(status)?;
        advance_step_decision(parsed).map_err(|()| {
            PyValueError::new_err(format!(
                "advance_step requires RUNNING, CHECKPOINTED or BLOCKED, got {} for {}",
                parsed.as_str(),
                wfi_id
            ))
        })
    }

    /// Frontiere PyO3 pour la garde en tete de `FlowEngine.resume`. `True`
    /// si le run n'est pas termine du point de vue du moteur de flows
    /// (donc qu'il reste quelque chose a reprendre).
    #[pyfunction]
    fn resume_is_allowed(status: &str) -> PyResult<bool> {
        Ok(resume_allowed(parse_status(status)?))
    }

    /// Frontiere PyO3 pour `check_output_against_contract`. `outputs` est
    /// `[(pin_id, contract), ...]` (`NodeContract.outputs`, deja derivees du
    /// blueprint cote Python). Memes messages, mot pour mot, que la
    /// reference Python.
    #[pyfunction]
    fn check_output_against_contract(
        node_id: &str,
        outputs: Vec<(String, String)>,
        output: Bound<'_, PyAny>,
    ) -> PyResult<Vec<String>> {
        let value = value_from_any(&output)?;
        Ok(check_output_against_contract_core(
            node_id, &outputs, &value,
        ))
    }

    /// Frontiere PyO3 pour `FlowEngine._current_node`. `checkpoint_state`
    /// distingue les trois cas possibles (voir `CheckpointPendingHead`) :
    /// `None` -> aucun checkpoint n'existe encore ; `Some(None)` -> un
    /// checkpoint existe, sa liste de pending est vide ; `Some(Some(id))`
    /// -> le premier node en attente du dernier checkpoint.
    #[pyfunction]
    #[allow(clippy::too_many_arguments)]
    fn current_node(
        status: &str,
        order: Vec<String>,
        last_step_failed: Option<String>,
        has_checkpoint: bool,
        checkpoint_pending_head: Option<String>,
        last_step_started: Option<String>,
    ) -> PyResult<Option<String>> {
        let parsed = parse_status(status)?;
        let head = if has_checkpoint {
            CheckpointPendingHead::PendingHead(checkpoint_pending_head)
        } else {
            CheckpointPendingHead::NoCheckpoints
        };
        Ok(current_node_core(
            parsed,
            &order,
            last_step_failed.as_deref(),
            head,
            last_step_started.as_deref(),
        ))
    }

    /// Frontiere PyO3 pour le calcul de `ResumeOutcome` dans
    /// `FlowEngine.resume`, une fois `faults` deja evalue. Retourne
    /// `(ok, finished, node_id, faults)`. Leve `ValueError` si `current_id`
    /// n'apparait pas dans `order` — un etat que la reference Python ne
    /// nommait pas (elle y aurait leve un `ValueError` non attribuable de
    /// `list.index`).
    #[pyfunction]
    fn resume_outcome(
        order: Vec<String>,
        current_id: &str,
        faults: Vec<String>,
    ) -> PyResult<(bool, bool, Option<String>, Vec<String>)> {
        let outcome = compute_resume_outcome(&order, current_id, faults).ok_or_else(|| {
            PyValueError::new_err(format!(
                "grimoire_flows_core.resume_outcome: current_id={current_id:?} absent de order={order:?}"
            ))
        })?;
        Ok((
            outcome.ok,
            outcome.finished,
            outcome.node_id,
            outcome.faults,
        ))
    }

    /// Frontiere PyO3 pour le decoupage completed/pending de
    /// `FlowEngine.status` (issue #414, voir le docstring du module).
    /// `checkpoint_completed` est `completed_steps` du dernier checkpoint
    /// connu (liste vide si aucun checkpoint), deja lu cote Python — ce
    /// crate ne fait aucune E/S.
    #[pyfunction]
    fn status_slices(
        order: Vec<String>,
        current_id: Option<String>,
        status: &str,
        checkpoint_completed: Vec<String>,
    ) -> PyResult<(Vec<String>, Vec<String>)> {
        let parsed = parse_status(status)?;
        Ok(status_slices_core(
            &order,
            current_id.as_deref(),
            parsed,
            &checkpoint_completed,
        ))
    }

    #[pymodule]
    fn grimoire_flows_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(parse_workflow_status, m)?)?;
        m.add_function(wrap_pyfunction!(is_flow_terminal_status, m)?)?;
        m.add_function(wrap_pyfunction!(allowed_transitions_from, m)?)?;
        m.add_function(wrap_pyfunction!(can_transition_status, m)?)?;
        m.add_function(wrap_pyfunction!(advance_step_needs_transition, m)?)?;
        m.add_function(wrap_pyfunction!(resume_is_allowed, m)?)?;
        m.add_function(wrap_pyfunction!(check_output_against_contract, m)?)?;
        m.add_function(wrap_pyfunction!(current_node, m)?)?;
        m.add_function(wrap_pyfunction!(resume_outcome, m)?)?;
        m.add_function(wrap_pyfunction!(status_slices, m)?)?;
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn order3() -> Vec<String> {
        vec!["a".to_string(), "b".to_string(), "c".to_string()]
    }

    // ── WorkflowStatus::parse ───────────────────────────────────────────

    #[test]
    fn parse_accepts_all_nine_known_values() {
        for s in [
            "created",
            "running",
            "checkpointed",
            "paused",
            "blocked",
            "aborted",
            "refused",
            "completed",
            "verified",
        ] {
            assert!(WorkflowStatus::parse(s).is_ok(), "{s} should parse");
        }
    }

    #[test]
    fn parse_rejects_unknown_status_never_defaults() {
        assert!(WorkflowStatus::parse("bogus").is_err());
        assert!(WorkflowStatus::parse("").is_err());
        assert!(WorkflowStatus::parse("Created").is_err()); // casse-sensible, comme la StrEnum Python
    }

    // ── is_flow_terminal (le defaut corrige) ────────────────────────────

    #[test]
    fn refused_is_flow_terminal() {
        assert!(is_flow_terminal(WorkflowStatus::Refused));
    }

    #[test]
    fn completed_verified_aborted_are_flow_terminal() {
        assert!(is_flow_terminal(WorkflowStatus::Completed));
        assert!(is_flow_terminal(WorkflowStatus::Verified));
        assert!(is_flow_terminal(WorkflowStatus::Aborted));
    }

    #[test]
    fn created_running_checkpointed_paused_blocked_are_not_flow_terminal() {
        for s in [
            WorkflowStatus::Created,
            WorkflowStatus::Running,
            WorkflowStatus::Checkpointed,
            WorkflowStatus::Paused,
            WorkflowStatus::Blocked,
        ] {
            assert!(!is_flow_terminal(s), "{s:?} ne devrait pas etre terminal");
        }
    }

    // ── allowed_transitions / can_transition (grille complete) ──────────

    #[test]
    fn transition_grid_matches_kernel_table() {
        use WorkflowStatus::*;
        let cases: &[(WorkflowStatus, &[WorkflowStatus])] = &[
            (Created, &[Running, Aborted, Refused]),
            (
                Running,
                &[Checkpointed, Paused, Blocked, Completed, Aborted, Refused],
            ),
            (Checkpointed, &[Running, Aborted, Refused]),
            (Paused, &[Running, Aborted, Refused]),
            (Blocked, &[Running, Aborted, Refused]),
            (Completed, &[Verified]),
            (Verified, &[]),
            (Aborted, &[]),
            (Refused, &[]),
        ];
        for (from, expected) in cases {
            assert_eq!(allowed_transitions(*from), *expected, "from {from:?}");
        }
    }

    #[test]
    fn terminal_kernel_statuses_have_zero_outgoing_transitions() {
        for s in [
            WorkflowStatus::Verified,
            WorkflowStatus::Aborted,
            WorkflowStatus::Refused,
        ] {
            assert!(allowed_transitions(s).is_empty());
        }
    }

    #[test]
    fn completed_still_has_one_kernel_transition_to_verified_but_is_flow_terminal() {
        // COMPLETED n'est pas un mur cote kernel (-> VERIFIED existe), mais
        // FlowEngine ne verifie jamais un run : is_flow_terminal(COMPLETED)
        // doit rester vrai malgre cette transition kernel vivante.
        assert_eq!(
            allowed_transitions(WorkflowStatus::Completed),
            &[WorkflowStatus::Verified]
        );
        assert!(is_flow_terminal(WorkflowStatus::Completed));
    }

    #[test]
    fn no_transition_ever_leaves_a_terminal_kernel_status() {
        for from in [
            WorkflowStatus::Verified,
            WorkflowStatus::Aborted,
            WorkflowStatus::Refused,
        ] {
            for to in [
                WorkflowStatus::Created,
                WorkflowStatus::Running,
                WorkflowStatus::Checkpointed,
                WorkflowStatus::Paused,
                WorkflowStatus::Blocked,
                WorkflowStatus::Completed,
                WorkflowStatus::Verified,
                WorkflowStatus::Aborted,
                WorkflowStatus::Refused,
            ] {
                assert!(
                    !can_transition(from, to),
                    "{from:?} -> {to:?} devrait etre interdit"
                );
            }
        }
    }

    #[test]
    fn abort_is_reachable_from_every_non_terminal_status() {
        for s in [
            WorkflowStatus::Created,
            WorkflowStatus::Running,
            WorkflowStatus::Checkpointed,
            WorkflowStatus::Paused,
            WorkflowStatus::Blocked,
        ] {
            assert!(
                can_transition(s, WorkflowStatus::Aborted),
                "{s:?} -> Aborted"
            );
        }
    }

    // ── advance_step_decision ────────────────────────────────────────────

    #[test]
    fn advance_step_from_checkpointed_or_blocked_transitions_to_running() {
        assert_eq!(
            advance_step_decision(WorkflowStatus::Checkpointed),
            Ok(true)
        );
        assert_eq!(advance_step_decision(WorkflowStatus::Blocked), Ok(true));
    }

    #[test]
    fn advance_step_from_running_stays_running() {
        assert_eq!(advance_step_decision(WorkflowStatus::Running), Ok(false));
    }

    #[test]
    fn advance_step_rejected_from_every_other_status() {
        for s in [
            WorkflowStatus::Created,
            WorkflowStatus::Paused,
            WorkflowStatus::Completed,
            WorkflowStatus::Aborted,
            WorkflowStatus::Refused,
            WorkflowStatus::Verified,
        ] {
            assert_eq!(
                advance_step_decision(s),
                Err(()),
                "{s:?} devrait etre rejete"
            );
        }
    }

    // ── resume_allowed ────────────────────────────────────────────────────

    #[test]
    fn resume_rejected_on_every_flow_terminal_status_including_refused() {
        for s in [
            WorkflowStatus::Completed,
            WorkflowStatus::Verified,
            WorkflowStatus::Aborted,
            WorkflowStatus::Refused,
        ] {
            assert!(
                !resume_allowed(s),
                "{s:?} ne devrait pas permettre resume()"
            );
        }
    }

    #[test]
    fn resume_allowed_on_every_in_flight_status() {
        for s in [
            WorkflowStatus::Created,
            WorkflowStatus::Running,
            WorkflowStatus::Checkpointed,
            WorkflowStatus::Paused,
            WorkflowStatus::Blocked,
        ] {
            assert!(resume_allowed(s), "{s:?} devrait permettre resume()");
        }
    }

    // ── check_output_against_contract_core ──────────────────────────────

    fn one_output() -> Vec<(String, String)> {
        vec![("out".to_string(), "c1".to_string())]
    }

    #[test]
    fn conforming_output_has_no_faults() {
        let output = Value::Map(vec![(
            "pins".to_string(),
            Value::Map(vec![(
                "out".to_string(),
                Value::Map(vec![("contract".to_string(), Value::Str("c1".to_string()))]),
            )]),
        )]);
        assert!(check_output_against_contract_core("a", &one_output(), &output).is_empty());
    }

    #[test]
    fn missing_pins_key_is_a_single_named_fault() {
        let output = Value::Map(vec![]);
        let faults = check_output_against_contract_core("a", &one_output(), &output);
        assert_eq!(faults.len(), 1);
        assert!(faults[0].contains("node=a"));
        assert!(faults[0].contains("sans objet 'pins'"));
    }

    #[test]
    fn output_not_a_dict_is_a_single_named_fault() {
        let output = Value::Str("pas un dict".to_string());
        let faults = check_output_against_contract_core("a", &one_output(), &output);
        assert_eq!(faults.len(), 1);
        assert!(faults[0].contains("sans objet 'pins'"));
    }

    #[test]
    fn pins_not_a_dict_is_a_single_named_fault() {
        let output = Value::Map(vec![("pins".to_string(), Value::List(vec![]))]);
        let faults = check_output_against_contract_core("a", &one_output(), &output);
        assert_eq!(faults.len(), 1);
        assert!(faults[0].contains("sans objet 'pins'"));
    }

    #[test]
    fn missing_pin_names_node_and_pin() {
        let output = Value::Map(vec![("pins".to_string(), Value::Map(vec![]))]);
        let faults = check_output_against_contract_core("a", &one_output(), &output);
        assert_eq!(faults.len(), 1);
        assert!(faults[0].contains("node=a"));
        assert!(faults[0].contains("pin=out"));
        assert!(faults[0].contains("absente de la sortie soumise"));
    }

    #[test]
    fn wrong_contract_names_produced_and_expected() {
        let output = Value::Map(vec![(
            "pins".to_string(),
            Value::Map(vec![(
                "out".to_string(),
                Value::Map(vec![(
                    "contract".to_string(),
                    Value::Str("mauvais".to_string()),
                )]),
            )]),
        )]);
        let faults = check_output_against_contract_core("a", &one_output(), &output);
        assert_eq!(faults.len(), 1);
        assert!(faults[0].contains("'mauvais'"));
        assert!(faults[0].contains("'c1'"));
    }

    #[test]
    fn pin_entry_not_a_dict_is_treated_as_absent() {
        let output = Value::Map(vec![(
            "pins".to_string(),
            Value::Map(vec![(
                "out".to_string(),
                Value::Str("pas-un-dict".to_string()),
            )]),
        )]);
        let faults = check_output_against_contract_core("a", &one_output(), &output);
        assert_eq!(faults.len(), 1);
        assert!(faults[0].contains("absente de la sortie soumise"));
    }

    #[test]
    fn contract_value_none_is_reported_as_none_repr() {
        let output = Value::Map(vec![(
            "pins".to_string(),
            Value::Map(vec![(
                "out".to_string(),
                Value::Map(vec![("contract".to_string(), Value::Null)]),
            )]),
        )]);
        let faults = check_output_against_contract_core("a", &one_output(), &output);
        assert_eq!(faults.len(), 1);
        assert!(faults[0].contains("None !="), "faults[0] = {}", faults[0]);
    }

    #[test]
    fn empty_contract_and_empty_output_satisfy_each_other() {
        let output = Value::Map(vec![("pins".to_string(), Value::Map(vec![]))]);
        assert!(check_output_against_contract_core("c", &[], &output).is_empty());
    }

    #[test]
    fn extra_pin_in_output_is_ignored_not_a_fault() {
        // Une pin en trop dans la sortie n'est jamais un defaut : seules les
        // pins DECLAREES par le contrat sont verifiees (meme comportement
        // que la reference Python, qui n'itere que sur `contract.outputs`).
        let output = Value::Map(vec![(
            "pins".to_string(),
            Value::Map(vec![
                (
                    "out".to_string(),
                    Value::Map(vec![("contract".to_string(), Value::Str("c1".to_string()))]),
                ),
                (
                    "bonus".to_string(),
                    Value::Map(vec![(
                        "contract".to_string(),
                        Value::Str("inattendu".to_string()),
                    )]),
                ),
            ]),
        )]);
        assert!(check_output_against_contract_core("a", &one_output(), &output).is_empty());
    }

    // ── current_node_core ────────────────────────────────────────────────

    #[test]
    fn current_node_terminal_status_is_always_none() {
        for s in [
            WorkflowStatus::Completed,
            WorkflowStatus::Verified,
            WorkflowStatus::Aborted,
            WorkflowStatus::Refused,
        ] {
            assert_eq!(
                current_node_core(
                    s,
                    &order3(),
                    Some("a"),
                    CheckpointPendingHead::PendingHead(Some("b".into())),
                    Some("a")
                ),
                None,
                "{s:?}"
            );
        }
    }

    #[test]
    fn current_node_blocked_returns_last_failed_step() {
        assert_eq!(
            current_node_core(
                WorkflowStatus::Blocked,
                &order3(),
                Some("b"),
                CheckpointPendingHead::NoCheckpoints,
                None
            ),
            Some("b".to_string())
        );
    }

    #[test]
    fn current_node_blocked_without_failed_event_falls_back_to_first_in_order() {
        assert_eq!(
            current_node_core(
                WorkflowStatus::Blocked,
                &order3(),
                None,
                CheckpointPendingHead::NoCheckpoints,
                None
            ),
            Some("a".to_string())
        );
    }

    #[test]
    fn current_node_blocked_on_empty_order_without_event_is_none() {
        assert_eq!(
            current_node_core(
                WorkflowStatus::Blocked,
                &[],
                None,
                CheckpointPendingHead::NoCheckpoints,
                None
            ),
            None
        );
    }

    #[test]
    fn current_node_checkpointed_returns_pending_head() {
        assert_eq!(
            current_node_core(
                WorkflowStatus::Checkpointed,
                &order3(),
                None,
                CheckpointPendingHead::PendingHead(Some("c".to_string())),
                None
            ),
            Some("c".to_string())
        );
    }

    #[test]
    fn current_node_checkpointed_with_empty_pending_is_none() {
        // Dernier node du run checkpointe : aucun pending -> None (le
        // resume() suivant appellera complete()).
        assert_eq!(
            current_node_core(
                WorkflowStatus::Checkpointed,
                &order3(),
                None,
                CheckpointPendingHead::PendingHead(None),
                None
            ),
            None
        );
    }

    #[test]
    fn current_node_checkpointed_without_any_checkpoint_falls_back_to_first_in_order() {
        assert_eq!(
            current_node_core(
                WorkflowStatus::Checkpointed,
                &order3(),
                None,
                CheckpointPendingHead::NoCheckpoints,
                None
            ),
            Some("a".to_string())
        );
    }

    #[test]
    fn current_node_running_returns_last_started_step() {
        assert_eq!(
            current_node_core(
                WorkflowStatus::Running,
                &order3(),
                None,
                CheckpointPendingHead::NoCheckpoints,
                Some("b")
            ),
            Some("b".to_string())
        );
    }

    #[test]
    fn current_node_running_without_started_event_falls_back_to_first_in_order() {
        // Le tout premier node d'un run, juste apres start() : aucun
        // STEP_STARTED n'a encore ete lu au moment de cet appel.
        assert_eq!(
            current_node_core(
                WorkflowStatus::Running,
                &order3(),
                None,
                CheckpointPendingHead::NoCheckpoints,
                None
            ),
            Some("a".to_string())
        );
    }

    #[test]
    fn current_node_created_behaves_like_running() {
        assert_eq!(
            current_node_core(
                WorkflowStatus::Created,
                &order3(),
                None,
                CheckpointPendingHead::NoCheckpoints,
                None
            ),
            Some("a".to_string())
        );
    }

    #[test]
    fn current_node_paused_behaves_like_running_dead_state_still_typed() {
        // PAUSED n'est jamais emis par RuntimeKernel (voir le docstring du
        // module) mais current_node_core doit statuer dessus quand meme :
        // meme branche que RUNNING/CREATED, comme le else generique cote
        // Python.
        assert_eq!(
            current_node_core(
                WorkflowStatus::Paused,
                &order3(),
                None,
                CheckpointPendingHead::NoCheckpoints,
                Some("c")
            ),
            Some("c".to_string())
        );
    }

    #[test]
    fn current_node_on_empty_order_with_no_events_is_none() {
        for s in [WorkflowStatus::Running, WorkflowStatus::Checkpointed] {
            let head = if s == WorkflowStatus::Checkpointed {
                CheckpointPendingHead::NoCheckpoints
            } else {
                CheckpointPendingHead::NoCheckpoints
            };
            assert_eq!(current_node_core(s, &[], None, head, None), None, "{s:?}");
        }
    }

    #[test]
    fn current_node_order_with_duplicate_ids_returns_the_duplicate_as_is() {
        // Un ordre avec doublons n'est pas normalise ici (topo_order, cote
        // Python, ne devrait jamais en produire un — mais current_node_core
        // ne suppose rien de plus que "une liste de chaines").
        let dup = vec!["a".to_string(), "a".to_string(), "b".to_string()];
        assert_eq!(
            current_node_core(
                WorkflowStatus::Running,
                &dup,
                None,
                CheckpointPendingHead::NoCheckpoints,
                None
            ),
            Some("a".to_string())
        );
    }

    // ── compute_resume_outcome ───────────────────────────────────────────

    #[test]
    fn resume_outcome_with_faults_is_not_ok_names_current_node() {
        let out = compute_resume_outcome(&order3(), "a", vec!["defaut".to_string()]).unwrap();
        assert!(!out.ok);
        assert!(!out.finished);
        assert_eq!(out.node_id, Some("a".to_string()));
        assert_eq!(out.faults, vec!["defaut".to_string()]);
    }

    #[test]
    fn resume_outcome_advances_to_next_pending_node() {
        let out = compute_resume_outcome(&order3(), "a", vec![]).unwrap();
        assert!(out.ok);
        assert!(!out.finished);
        assert_eq!(out.node_id, Some("b".to_string()));
    }

    #[test]
    fn resume_outcome_on_last_node_is_finished() {
        let out = compute_resume_outcome(&order3(), "c", vec![]).unwrap();
        assert!(out.ok);
        assert!(out.finished);
        assert_eq!(out.node_id, Some("c".to_string()));
    }

    #[test]
    fn resume_outcome_current_id_absent_from_order_is_none() {
        assert!(compute_resume_outcome(&order3(), "z", vec![]).is_none());
    }

    #[test]
    fn resume_outcome_single_node_order_finishes_immediately() {
        let single = vec!["only".to_string()];
        let out = compute_resume_outcome(&single, "only", vec![]).unwrap();
        assert!(out.finished);
    }

    // ── status_slices_core ───────────────────────────────────────────────

    #[test]
    fn status_slices_mid_run() {
        let (completed, pending) =
            status_slices_core(&order3(), Some("b"), WorkflowStatus::Running, &[]);
        assert_eq!(completed, vec!["a".to_string()]);
        assert_eq!(pending, vec!["c".to_string()]);
    }

    #[test]
    fn status_slices_finished_run_all_completed_none_pending() {
        let (completed, pending) =
            status_slices_core(&order3(), None, WorkflowStatus::Completed, &[]);
        assert_eq!(completed, order3());
        assert!(pending.is_empty());
    }

    #[test]
    fn status_slices_verified_run_all_completed_none_pending() {
        let (completed, pending) =
            status_slices_core(&order3(), None, WorkflowStatus::Verified, &[]);
        assert_eq!(completed, order3());
        assert!(pending.is_empty());
    }

    #[test]
    fn status_slices_last_node_current_has_no_pending() {
        let (completed, pending) =
            status_slices_core(&order3(), Some("c"), WorkflowStatus::Running, &[]);
        assert_eq!(completed, vec!["a".to_string(), "b".to_string()]);
        assert!(pending.is_empty());
    }

    #[test]
    fn status_slices_current_id_not_in_order_treated_as_finished() {
        // Defensif, pas dans le perimetre de l'issue #414 : un current_id
        // hors de `order` (ne devrait jamais arriver) retombe sur `order`
        // en entier, comme avant.
        let (completed, pending) =
            status_slices_core(&order3(), Some("inconnu"), WorkflowStatus::Running, &[]);
        assert_eq!(completed, order3());
        assert!(pending.is_empty());
    }

    #[test]
    fn status_slices_empty_order() {
        let (completed, pending) = status_slices_core(&[], None, WorkflowStatus::Completed, &[]);
        assert!(completed.is_empty());
        assert!(pending.is_empty());
    }

    #[test]
    fn status_slices_aborted_run_before_any_progress_is_empty() {
        // Correctif de l'issue #414 : un abandon avant tout `resume()` n'a
        // aucun checkpoint — `completed_nodes` doit rester vide, jamais
        // `order` en entier.
        let (completed, pending) =
            status_slices_core(&order3(), None, WorkflowStatus::Aborted, &[]);
        assert!(completed.is_empty(), "corrige : aucun node reellement fait");
        assert!(pending.is_empty());
    }

    #[test]
    fn status_slices_aborted_run_after_k_nodes_reflects_last_checkpoint() {
        // Correctif de l'issue #414 : un abandon apres k `resume()` reussis
        // rend les k nodes reellement checkpointes, pas une position
        // deduite de `current_node`.
        let checkpointed = vec!["a".to_string()];
        let (completed, pending) =
            status_slices_core(&order3(), None, WorkflowStatus::Aborted, &checkpointed);
        assert_eq!(completed, vec!["a".to_string()]);
        assert!(pending.is_empty());
    }

    #[test]
    fn status_slices_refused_run_before_any_progress_is_empty() {
        // Meme correctif, meme verdict pour REFUSED (plafond MAST) que pour
        // ABORTED — les deux sont terminaux sans reprise possible.
        let (completed, pending) =
            status_slices_core(&order3(), None, WorkflowStatus::Refused, &[]);
        assert!(completed.is_empty());
        assert!(pending.is_empty());
    }
}
