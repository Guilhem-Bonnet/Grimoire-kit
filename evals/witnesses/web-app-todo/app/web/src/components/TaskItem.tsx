import { useState } from 'react'
import type { Task } from '../types'

export default function TaskItem({
  task,
  onChanged,
}: {
  task: Task
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)

  async function toggleDone() {
    setBusy(true)
    try {
      if (task.done) {
        await fetch(`/api/tasks/${task.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ done: false }),
        })
      } else {
        await fetch(`/api/tasks/${task.id}/complete`, { method: 'POST' })
      }
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className={task.done ? 'task task-done' : 'task'}>
      <label className="task-main">
        <input
          type="checkbox"
          checked={task.done}
          disabled={busy}
          onChange={toggleDone}
        />
        <span className="task-title">{task.title}</span>
      </label>
      <span className="task-tags">
        {task.tags.map((tag) => (
          <span key={tag.id} className="tag">
            {tag.name}
          </span>
        ))}
      </span>
      <time className="task-date" dateTime={task.created_at}>
        {task.created_at}
      </time>
    </li>
  )
}
