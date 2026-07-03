DELETE FROM task_tags;
DELETE FROM tags;
DELETE FROM tasks;

SELECT setval('tasks_id_seq', 1, FALSE);
SELECT setval('tags_id_seq', 1, FALSE);
