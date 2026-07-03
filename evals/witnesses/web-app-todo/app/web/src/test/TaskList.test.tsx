import { render, screen } from '@testing-library/react'
import TaskList from '../components/TaskList'
import type { Task } from '../types'

const tasks: Task[] = [
  {
    id: 1,
    title: 'Acheter du pain',
    done: false,
    created_at: '2026-06-19T17:42:00Z',
    tags: [{ id: 2, name: 'Perso' }],
  },
  {
    id: 2,
    title: 'Relire le contrat',
    done: true,
    created_at: '2026-06-20T09:05:00Z',
    tags: [],
  },
]

function okJson(body: unknown) {
  return { ok: true, status: 200, json: async () => body }
}

describe('TaskList', () => {
  it('affiche les tâches et leurs tags', async () => {
    const fetchMock = vi.fn(async () => okJson(tasks))
    vi.stubGlobal('fetch', fetchMock)

    render(<TaskList refreshKey={0} />)

    expect(await screen.findByText('Acheter du pain')).toBeInTheDocument()
    expect(screen.getByText('Relire le contrat')).toBeInTheDocument()
    expect(screen.getByText('Perso')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/tasks')
  })

  it('affiche une erreur quand le chargement échoue', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 500,
      json: async () => ({}),
    }))
    vi.stubGlobal('fetch', fetchMock)

    render(<TaskList refreshKey={0} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Impossible de charger les tâches.',
    )
  })
})
