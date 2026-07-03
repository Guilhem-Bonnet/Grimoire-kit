import { useState, type FormEvent } from 'react'

export default function AddTaskForm({ onAdded }: { onAdded: () => void }) {
  const [title, setTitle] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = title.trim()
    if (!trimmed) {
      return
    }
    try {
      const res = await fetch('/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: trimmed }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({ error: '' }))
        setError(body.error || `HTTP ${res.status}`)
        return
      }
      setTitle('')
      setError(null)
      onAdded()
    } catch {
      setError('Le serveur est injoignable.')
    }
  }

  return (
    <form className="add-task" onSubmit={handleSubmit}>
      <input
        type="text"
        value={title}
        placeholder="Nouvelle tâche"
        aria-label="Titre de la tâche"
        onChange={(e) => setTitle(e.target.value)}
      />
      <button type="submit">Ajouter</button>
      {error && <p role="alert">{error}</p>}
    </form>
  )
}
