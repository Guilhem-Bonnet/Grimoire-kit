import { useState } from 'react'
import AddTaskForm from './components/AddTaskForm'
import TaskList from './components/TaskList'

export default function App() {
  const [refreshKey, setRefreshKey] = useState(0)

  return (
    <main className="app">
      <h1>Mes tâches</h1>
      <AddTaskForm onAdded={() => setRefreshKey((k) => k + 1)} />
      <TaskList refreshKey={refreshKey} />
    </main>
  )
}
