package main

import (
	"context"
	"errors"
	"time"
)

// ErrNotFound est renvoyée quand une tâche n'existe pas.
var ErrNotFound = errors.New("task not found")

type Tag struct {
	ID   int    `json:"id"`
	Name string `json:"name"`
}

type Task struct {
	ID        int       `json:"id"`
	Title     string    `json:"title"`
	Done      bool      `json:"done"`
	CreatedAt time.Time `json:"created_at"`
	Tags      []Tag     `json:"tags"`
}

// Store abstrait l'accès aux données pour les handlers HTTP.
type Store interface {
	ListTasks(ctx context.Context) ([]Task, error)
	TagsForTask(ctx context.Context, taskID int) ([]Tag, error)
	CreateTask(ctx context.Context, title string) (Task, error)
	UpdateTask(ctx context.Context, id int, title *string, done *bool) (Task, error)
	CompleteTask(ctx context.Context, id int) (Task, error)
}
