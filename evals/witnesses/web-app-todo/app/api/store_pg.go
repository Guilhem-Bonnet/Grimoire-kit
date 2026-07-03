package main

import (
	"context"
	"database/sql"
	"errors"
)

type PGStore struct {
	db *sql.DB
}

func NewPGStore(db *sql.DB) *PGStore {
	return &PGStore{db: db}
}

func (s *PGStore) ListTasks(ctx context.Context) ([]Task, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT id, title, done, created_at FROM tasks ORDER BY created_at DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	tasks := []Task{}
	for rows.Next() {
		var t Task
		if err := rows.Scan(&t.ID, &t.Title, &t.Done, &t.CreatedAt); err != nil {
			return nil, err
		}
		t.CreatedAt = t.CreatedAt.UTC()
		t.Tags = []Tag{}
		tasks = append(tasks, t)
	}
	return tasks, rows.Err()
}

func (s *PGStore) TagsForTask(ctx context.Context, taskID int) ([]Tag, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT t.id, t.name
		   FROM tags t
		   JOIN task_tags tt ON tt.tag_id = t.id
		  WHERE tt.task_id = $1
		  ORDER BY t.name`, taskID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	tags := []Tag{}
	for rows.Next() {
		var tag Tag
		if err := rows.Scan(&tag.ID, &tag.Name); err != nil {
			return nil, err
		}
		tags = append(tags, tag)
	}
	return tags, rows.Err()
}

func (s *PGStore) CreateTask(ctx context.Context, title string) (Task, error) {
	var t Task
	err := s.db.QueryRowContext(ctx,
		`INSERT INTO tasks (title) VALUES ($1)
		 RETURNING id, title, done, created_at`, title).
		Scan(&t.ID, &t.Title, &t.Done, &t.CreatedAt)
	if err != nil {
		return Task{}, err
	}
	t.CreatedAt = t.CreatedAt.UTC()
	t.Tags = []Tag{}
	return t, nil
}

func (s *PGStore) UpdateTask(ctx context.Context, id int, title *string, done *bool) (Task, error) {
	var t Task
	err := s.db.QueryRowContext(ctx,
		`UPDATE tasks
		    SET title = COALESCE($2, title),
		        done  = COALESCE($3, done)
		  WHERE id = $1
		 RETURNING id, title, done, created_at`, id, title, done).
		Scan(&t.ID, &t.Title, &t.Done, &t.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return Task{}, ErrNotFound
	}
	if err != nil {
		return Task{}, err
	}
	t.CreatedAt = t.CreatedAt.UTC()
	t.Tags = []Tag{}
	return t, nil
}

func (s *PGStore) CompleteTask(ctx context.Context, id int) (Task, error) {
	var t Task
	err := s.db.QueryRowContext(ctx,
		`UPDATE tasks SET done = TRUE WHERE id = $1
		 RETURNING id, title, done, created_at`, id).
		Scan(&t.ID, &t.Title, &t.Done, &t.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return Task{}, ErrNotFound
	}
	if err != nil {
		return Task{}, err
	}
	t.CreatedAt = t.CreatedAt.UTC()
	t.Tags = []Tag{}
	return t, nil
}
