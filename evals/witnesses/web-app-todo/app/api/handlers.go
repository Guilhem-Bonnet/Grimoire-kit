package main

import (
	"encoding/json"
	"errors"
	"io/ioutil"
	"log"
	"net/http"
	"strconv"
	"strings"
	"unicode/utf8"
)

type Server struct {
	store Store
}

func NewServer(store Store) http.Handler {
	s := &Server{store: store}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /tasks", s.listTasks)
	mux.HandleFunc("POST /tasks", s.createTask)
	mux.HandleFunc("PUT /tasks/{id}", s.updateTask)
	mux.HandleFunc("POST /tasks/{id}/complete", s.completeTask)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	})
	return mux
}

func (s *Server) listTasks(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	tasks, err := s.store.ListTasks(ctx)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "cannot list tasks")
		return
	}
	for i := range tasks {
		tags, err := s.store.TagsForTask(ctx, tasks[i].ID)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "cannot load tags")
			return
		}
		for j := range tags {
			tags[j].Name = strings.Title(tags[j].Name)
		}
		tasks[i].Tags = tags
	}
	writeJSON(w, http.StatusOK, tasks)
}

func (s *Server) createTask(w http.ResponseWriter, r *http.Request) {
	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		writeError(w, http.StatusBadRequest, "cannot read body")
		return
	}
	var payload struct {
		Title string `json:"title"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON")
		return
	}
	payload.Title = strings.TrimSpace(payload.Title)
	if payload.Title == "" {
		writeError(w, http.StatusBadRequest, "title is required")
		return
	}
	if utf8.RuneCountInString(payload.Title) > 200 {
		writeError(w, http.StatusBadRequest, "title must be 200 characters or fewer")
		return
	}
	task, err := s.store.CreateTask(r.Context(), payload.Title)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "cannot create task")
		return
	}
	writeJSON(w, http.StatusCreated, task)
}

func (s *Server) updateTask(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.Atoi(r.PathValue("id"))
	if err != nil || id <= 0 {
		writeError(w, http.StatusBadRequest, "invalid task id")
		return
	}
	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		writeError(w, http.StatusBadRequest, "cannot read body")
		return
	}
	var payload struct {
		Title *string `json:"title"`
		Done  *bool   `json:"done"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON")
		return
	}
	if payload.Title == nil && payload.Done == nil {
		writeError(w, http.StatusBadRequest, "nothing to update")
		return
	}
	if payload.Title != nil {
		trimmed := strings.TrimSpace(*payload.Title)
		if trimmed == "" {
			writeError(w, http.StatusBadRequest, "title cannot be empty")
			return
		}
		if utf8.RuneCountInString(trimmed) > 200 {
			writeError(w, http.StatusBadRequest, "title must be 200 characters or fewer")
			return
		}
		payload.Title = &trimmed
	}
	task, err := s.store.UpdateTask(r.Context(), id, payload.Title, payload.Done)
	if errors.Is(err, ErrNotFound) {
		writeError(w, http.StatusNotFound, "task not found")
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "cannot update task")
		return
	}
	writeJSON(w, http.StatusOK, task)
}

func (s *Server) completeTask(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.Atoi(r.PathValue("id"))
	if err != nil || id <= 0 {
		writeError(w, http.StatusBadRequest, "invalid task id")
		return
	}
	task, err := s.store.CompleteTask(r.Context(), id)
	if errors.Is(err, ErrNotFound) {
		writeError(w, http.StatusNotFound, "task not found")
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "cannot complete task")
		return
	}
	writeJSON(w, http.StatusOK, task)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		log.Printf("write response: %v", err)
	}
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}
