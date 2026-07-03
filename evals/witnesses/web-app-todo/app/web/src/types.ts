export interface Tag {
  id: number
  name: string
}

export interface Task {
  id: number
  title: string
  done: boolean
  created_at: string
  tags: Tag[]
}
