INSERT INTO tasks (id, title, done, created_at) VALUES
    (1,  'Préparer la revue de sprint',        FALSE, '2026-06-18T08:15:00Z'),
    (2,  'Acheter du pain',                    TRUE,  '2026-06-19T17:42:00Z'),
    (3,  'Relire le contrat fournisseur',      FALSE, '2026-06-20T09:05:00Z'),
    (4,  'Réserver la salle de réunion',       TRUE,  '2026-06-21T11:30:00Z'),
    (5,  'Mettre à jour les dépendances',      FALSE, '2026-06-23T14:20:00Z'),
    (6,  'Planifier les entretiens annuels',   FALSE, '2026-06-24T10:00:00Z'),
    (7,  'Payer la facture d''électricité',    TRUE,  '2026-06-26T19:55:00Z'),
    (8,  'Rédiger le compte rendu d''audit',   FALSE, '2026-06-28T16:10:00Z'),
    (9,  'Prendre rendez-vous chez le dentiste', FALSE, '2026-06-30T07:45:00Z'),
    (10, 'Archiver les tickets fermés',        FALSE, '2026-07-01T13:25:00Z');

SELECT setval('tasks_id_seq', (SELECT max(id) FROM tasks));

INSERT INTO tags (id, name) VALUES
    (1, 'travail'),
    (2, 'perso'),
    (3, 'urgent'),
    (4, 'admin');

SELECT setval('tags_id_seq', (SELECT max(id) FROM tags));

INSERT INTO task_tags (task_id, tag_id) VALUES
    (1, 1),
    (1, 3),
    (2, 2),
    (3, 1),
    (3, 4),
    (5, 1),
    (6, 1),
    (7, 2),
    (7, 4),
    (8, 1),
    (8, 3),
    (9, 2),
    (10, 4);
