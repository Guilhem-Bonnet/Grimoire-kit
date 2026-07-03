import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import AddTaskForm from '../components/AddTaskForm'

describe('AddTaskForm', () => {
  it('envoie la nouvelle tâche et notifie le parent', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 201,
      json: async () => ({
        id: 3,
        title: 'Nouvelle tâche',
        done: false,
        created_at: '2026-07-02T09:00:00Z',
        tags: [],
      }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    const onAdded = vi.fn()

    render(<AddTaskForm onAdded={onAdded} />)

    fireEvent.change(screen.getByLabelText('Titre de la tâche'), {
      target: { value: 'Nouvelle tâche' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Ajouter' }))

    await waitFor(() => expect(onAdded).toHaveBeenCalledTimes(1))
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tasks',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ title: 'Nouvelle tâche' }),
      }),
    )
    expect(screen.getByLabelText('Titre de la tâche')).toHaveValue('')
  })

  it('affiche le message d\'erreur renvoyé par l\'API', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 400,
      json: async () => ({ error: 'title is required' }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    const onAdded = vi.fn()

    render(<AddTaskForm onAdded={onAdded} />)

    fireEvent.change(screen.getByLabelText('Titre de la tâche'), {
      target: { value: 'x' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Ajouter' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'title is required',
    )
    expect(onAdded).not.toHaveBeenCalled()
  })
})
