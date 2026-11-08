export type EmployeeRole =
  | 'Admin'
  | 'Loan Officer'
  | 'Underwriter'
  | 'Fraud Investigator'
  | 'Auditor'

export type Employee = {
  id: number
  email: string
  employeeId: string
  name: string
  role: EmployeeRole
  active: boolean
}

export type AuditEvent = {
  id: number
  actor: string | null
  event: string
  resource: string
  details: string
  ipAddress: string
  createdAt: string
}

export type HistoricalRecord = {
  dealId: string
  applicationDate: string
  business: string
  industry: string
  amount: string
  product: string
  status: string
  repaymentStatus: string
}

export const employeeRoles: EmployeeRole[] = [
  'Admin',
  'Loan Officer',
  'Underwriter',
  'Fraud Investigator',
  'Auditor',
]

export async function authRequest(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    cache: 'no-store',
  })
  if (response.status === 401 && path !== '/api/auth/login') {
    window.dispatchEvent(new Event('dealpilot:unauthorized'))
  }
  return response
}

export async function errorMessage(response: Response, fallback: string): Promise<string> {
  const body = await response.json().catch(() => null) as {
    detail?: unknown
    error?: string
  } | null
  if (typeof body?.detail === 'string') return body.detail
  if (Array.isArray(body?.detail)) {
    const messages = body.detail
      .map((item) => typeof item === 'object' && item !== null && 'msg' in item
        ? String(item.msg)
        : '')
      .filter(Boolean)
    if (messages.length) return messages.join('; ')
  }
  return body?.error ?? `${fallback} (${response.status}).`
}

export async function getCurrentEmployee(): Promise<Employee | null> {
  const response = await authRequest('/api/auth/me')
  if (response.status === 401) return null
  if (!response.ok) throw new Error(await errorMessage(response, 'Unable to verify your session'))
  const body = await response.json() as { employee: Employee }
  return body.employee
}

export async function login(email: string, password: string): Promise<Employee> {
  const response = await authRequest('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  if (!response.ok) throw new Error(await errorMessage(response, 'Sign-in failed'))
  const body = await response.json() as { employee: Employee }
  return body.employee
}

export async function logout(): Promise<void> {
  const response = await authRequest('/api/auth/logout', { method: 'POST' })
  if (!response.ok) throw new Error(await errorMessage(response, 'Sign-out failed'))
}

export async function fetchAuditEvents(): Promise<AuditEvent[]> {
  const response = await authRequest('/api/audit')
  if (!response.ok) throw new Error(await errorMessage(response, 'Unable to load audit events'))
  const body = await response.json() as { events: AuditEvent[] }
  return body.events
}

export async function fetchHistoricalRecords(): Promise<HistoricalRecord[]> {
  const response = await authRequest('/api/history')
  if (!response.ok) throw new Error(await errorMessage(response, 'Unable to load historical records'))
  const body = await response.json() as { records: HistoricalRecord[] }
  return body.records
}

export async function fetchEmployees(): Promise<{ roles: EmployeeRole[]; users: Employee[] }> {
  const response = await authRequest('/api/admin/users')
  if (!response.ok) throw new Error(await errorMessage(response, 'Unable to load employees'))
  return response.json() as Promise<{ roles: EmployeeRole[]; users: Employee[] }>
}

export async function createEmployee(payload: {
  email: string
  employeeId: string
  name: string
  role: EmployeeRole
  password: string
}): Promise<Employee> {
  const response = await authRequest('/api/admin/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(await errorMessage(response, 'Unable to create employee'))
  const body = await response.json() as { user: Employee }
  return body.user
}

export async function updateEmployee(
  id: number,
  payload: { role?: EmployeeRole; active?: boolean; password?: string },
): Promise<Employee> {
  const response = await authRequest(`/api/admin/users/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(await errorMessage(response, 'Unable to update employee'))
  const body = await response.json() as { user: Employee }
  return body.user
}
