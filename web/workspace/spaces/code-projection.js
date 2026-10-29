// Lecture du texte d'une projection de code (`memory/projections.py`, `_code_node_text`).
//
// Le texte est une suite de lignes « Clé: valeur » puis, optionnellement, un
// bloc « Docstring: » et un bloc « Source excerpt: » qui garde l'indentation de
// la source. Le parseur échoue ouvert : tout texte non reconnu renvoie `null`
// (l'appelant retombe sur le rendu brut), jamais d'exception.

const HEAD = /^([^:\n]+): (.+)$/;
const FIELD = /^(File|Module|Kind|Lines|Test|Public): ?(.*)$/;

/** @returns {{label:string,name:string,file:string,module:string,kind:string,lines:string,test:boolean,public:boolean,signature:string,docstring:string,source:string}|null} */
export function parseCodeProjection(text) {
  try {
    if (typeof text !== 'string') return null;
    const rows = text.replace(/\r\n?/g, '\n').split('\n');
    const head = HEAD.exec(rows[0] || '');
    if (!head) return null;
    const fields = {};
    let i = 1;
    for (; i < rows.length; i += 1) {
      const m = FIELD.exec(rows[i]);
      if (!m) break;
      fields[m[1]] = m[2].trim();
    }
    if (!fields.File || !fields.Kind || !/^\d+-\d+$/.test(fields.Lines || '')) return null;
    let docstring = '';
    let source = '';
    if (rows[i] === 'Docstring:') {
      const end = rows.indexOf('Source excerpt:', i + 1);
      docstring = rows.slice(i + 1, end < 0 ? rows.length : end).join('\n').trim();
      i = end < 0 ? rows.length : end;
    }
    if (rows[i] === 'Source excerpt:') source = rows.slice(i + 1).join('\n').replace(/\s+$/, '');
    else if (i < rows.length && rows[i] !== '') return null;
    const first = source.split('\n').find((l) => l.trim()) || '';
    return {
      label: head[1].trim(),
      name: head[2].trim(),
      file: fields.File,
      module: fields.Module || '',
      kind: fields.Kind,
      lines: fields.Lines,
      test: fields.Test === 'yes',
      public: fields.Public === 'yes',
      signature: first.trim(),
      docstring,
      source,
    };
  } catch {
    return null;
  }
}
