package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// fakeStore est un Store en mémoire pour tester les handlers sans PostgreSQL.
// TagQueries compte les appels à TagsForTask (une requête SQL chacun dans
// l'implémentation réelle).
type fakeStore struct {
	tasks      []Task
	tags       map[int][]Tag
	nextID     int
	TagQueries int
}

func newFakeStore() *fakeStore {
	now := time.Date(2026, 7, 1, 12, 0, 0, 0, time.UTC)
	return &fakeStore{
		tasks: []Task{
			{ID: 1, Title: "Acheter du pain", Done: false, CreatedAt: now, Tags: []Tag{}},
			{ID: 2, Title: "Relire le contrat", Done: true, CreatedAt: now.Add(time.Hour), Tags: []Tag{}},
		},
		tags: map[int][]Tag{
			1: {{ID: 1, Name: "perso"}},
			2: {{ID: 2, Name: "travail"}, {ID: 3, Name: "urgent"}},
		},
		nextID: 3,
	}
}

func (f *fakeStore) ListTasks(_ context.Context) ([]Task, error) {
	out := make([]Task, len(f.tasks))
	copy(out, f.tasks)
	return out, nil
}

func (f *fakeStore) TagsForTask(_ context.Context, taskID int) ([]Tag, error) {
	f.TagQueries++
	tags := f.tags[taskID]
	out := make([]Tag, len(tags))
	copy(out, tags)
	return out, nil
}

func (f *fakeStore) CreateTask(_ context.Context, title string) (Task, error) {
	t := Task{
		ID:        f.nextID,
		Title:     title,
		Done:      false,
		CreatedAt: time.Date(2026, 7, 2, 9, 0, 0, 0, time.UTC),
		Tags:      []Tag{},
	}
	f.nextID++
	f.tasks = append(f.tasks, t)
	return t, nil
}

func (f *fakeStore) UpdateTask(_ context.Context, id int, title *string, done *bool) (Task, error) {
	for i := range f.tasks {
		if f.tasks[i].ID == id {
			if title != nil {
				f.tasks[i].Title = *title
			}
			if done != nil {
				f.tasks[i].Done = *done
			}
			return f.tasks[i], nil
		}
	}
	return Task{}, ErrNotFound
}

func (f *fakeStore) CompleteTask(_ context.Context, id int) (Task, error) {
	for i := range f.tasks {
		if f.tasks[i].ID == id {
			f.tasks[i].Done = true
			return f.tasks[i], nil
		}
	}
	return Task{}, ErrNotFound
}

func doRequest(t *testing.T, handler http.Handler, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	var req *http.Request
	if body == "" {
		req = httptest.NewRequest(method, path, nil)
	} else {
		req = httptest.NewRequest(method, path, strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
	}
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	return rec
}

func TestListTasksReturnsTasksWithTags(t *testing.T) {
	store := newFakeStore()
	handler := NewServer(store)

	rec := doRequest(t, handler, http.MethodGet, "/tasks", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	var tasks []Task
	if err := json.Unmarshal(rec.Body.Bytes(), &tasks); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if len(tasks) != 2 {
		t.Fatalf("expected 2 tasks, got %d", len(tasks))
	}
	if len(tasks[1].Tags) != 2 {
		t.Fatalf("expected 2 tags on second task, got %d", len(tasks[1].Tags))
	}
	if tasks[0].Tags[0].Name != "Perso" {
		t.Errorf("expected title-cased tag name, got %q", tasks[0].Tags[0].Name)
	}
}

func TestCreateTaskNominal(t *testing.T) {
	store := newFakeStore()
	handler := NewServer(store)

	rec := doRequest(t, handler, http.MethodPost, "/tasks", `{"title":"Nouvelle tâche"}`)
	if rec.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", rec.Code, rec.Body.String())
	}
	var task Task
	if err := json.Unmarshal(rec.Body.Bytes(), &task); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if task.Title != "Nouvelle tâche" {
		t.Errorf("expected title preserved, got %q", task.Title)
	}
	if task.Done {
		t.Error("new task must not be done")
	}
}

func TestCreateTaskRejectsEmptyTitle(t *testing.T) {
	store := newFakeStore()
	handler := NewServer(store)

	rec := doRequest(t, handler, http.MethodPost, "/tasks", `{"title":"   "}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", rec.Code, rec.Body.String())
	}
	var body map[string]string
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if body["error"] == "" {
		t.Error("expected an error message")
	}
}

func TestUpdateTaskTitle(t *testing.T) {
	store := newFakeStore()
	handler := NewServer(store)

	rec := doRequest(t, handler, http.MethodPut, "/tasks/1", `{"title":"Acheter des croissants"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	var task Task
	if err := json.Unmarshal(rec.Body.Bytes(), &task); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if task.Title != "Acheter des croissants" {
		t.Errorf("expected updated title, got %q", task.Title)
	}
}

func TestUpdateTaskNotFound(t *testing.T) {
	store := newFakeStore()
	handler := NewServer(store)

	rec := doRequest(t, handler, http.MethodPut, "/tasks/999", `{"done":true}`)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestCompleteTask(t *testing.T) {
	store := newFakeStore()
	handler := NewServer(store)

	rec := doRequest(t, handler, http.MethodPost, "/tasks/1/complete", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	var task Task
	if err := json.Unmarshal(rec.Body.Bytes(), &task); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if !task.Done {
		t.Error("expected task to be done")
	}
}
