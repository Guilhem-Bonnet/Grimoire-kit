import { useCallback, useEffect, useState } from 'react'
import type { Task } from '../types'
import TaskItem from './TaskItem'

export default function TaskList({ refreshKey }: { refreshKey: number }) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/tasks')
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`)
      }
      setTasks(await res.json())
      setError(null)
    } catch {
      setError('Impossible de charger les tâches.')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load, refreshKey])

  if (error) {
    return <p role="alert">{error}</p>
  }
  return (
    <ul className="task-list">
      {tasks.map((task) => (
        <TaskItem key={task.id} task={task} onChanged={load} />
      ))}
    </ul>
  )
}
