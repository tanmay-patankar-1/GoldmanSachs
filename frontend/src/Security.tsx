import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { RefreshCw, Shield, UserPlus } from 'lucide-react'
import {
  createEmployee,
  employeeRoles,
  fetchAuditEvents,
  fetchEmployees,
  fetchHistoricalRecords,
  updateEmployee,
} from './data/authApi'
import type { AuditEvent, Employee, EmployeeRole, HistoricalRecord } from './data/authApi'
import './Security.css'

export function UserManagement({
  currentEmployee,
  onLogout,
}: {
  currentEmployee: Employee
  onLogout: () => void
}) {
  const [users, setUsers] = useState<Employee[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [resetPasswords, setResetPasswords] = useState<Record<number, string>>({})
  const [form, setForm] = useState({
    email: '',
    employeeId: '',
    name: '',
    role: 'Loan Officer' as EmployeeRole,
    password: '',
  })

  const reload = async () => {
    try {
      setUsers((await fetchEmployees()).users)
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load employees.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let active = true
    void fetchEmployees()
      .then(({ users: loadedUsers }) => {
        if (active) setUsers(loadedUsers)
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Unable to load employees.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    setNotice('')
    try {
      await createEmployee(form)
      setForm({ email: '', employeeId: '', name: '', role: 'Loan Officer', password: '' })
      setNotice('Employee account created.')
      await reload()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to create employee.')
    }
  }

  const changeUser = async (user: Employee, update: { role?: EmployeeRole; active?: boolean }) => {
    setError('')
    setNotice('')
    try {
      await updateEmployee(user.id, update)
      setNotice(`Updated access for ${user.name}.`)
      await reload()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to update employee.')
    }
  }

  const resetPassword = async (event: FormEvent<HTMLFormElement>, user: Employee) => {
    event.preventDefault()
    const password = resetPasswords[user.id] ?? ''
    setError('')
    setNotice('')
    try {
      await updateEmployee(user.id, { password })
      setResetPasswords((current) => ({ ...current, [user.id]: '' }))
      if (user.id === currentEmployee.id) {
        onLogout()
        return
      }
      setNotice(`Password reset for ${user.name}; their existing sessions were revoked.`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to reset employee password.')
    }
  }

  return (
    <div className="security-content">
      <div className="page-heading">
        <div>
          <p>ADMINISTRATION</p>
          <h1>Employee access</h1>
          <span>Manage employee accounts, roles, and active access.</span>
        </div>
        <span className="security-role-chip"><Shield size={14} /> {currentEmployee.role}</span>
      </div>

      {error && <div className="security-message security-message-error" role="alert">{error}</div>}
      {notice && <div className="security-message" role="status">{notice}</div>}

      <section className="security-panel">
        <div className="security-panel-heading">
          <div>
            <h2><UserPlus size={17} /> Add employee</h2>
            <p>New passwords must be at least 12 characters.</p>
          </div>
        </div>
        <form className="security-create-form" onSubmit={(event) => void submit(event)}>
          <label>
            Employee name
            <input required maxLength={120} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
          </label>
          <label>
            Company email
            <input
              type="email"
              required
              maxLength={320}
              value={form.email}
              onChange={(event) => {
                const email = event.target.value
                setForm({ ...form, email, employeeId: email.split('@')[0].toLowerCase() })
              }}
            />
          </label>
          <label>
            Workflow employee ID
            <input required maxLength={80} value={form.employeeId} onChange={(event) => setForm({ ...form, employeeId: event.target.value })} />
          </label>
          <label>
            Role
            <select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value as EmployeeRole })}>
              {employeeRoles.map((role) => <option key={role}>{role}</option>)}
            </select>
          </label>
          <label>
            Initial password
            <input type="password" autoComplete="new-password" minLength={12} maxLength={1024} required value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} />
          </label>
          <button className="button button-primary" type="submit">Create account</button>
        </form>
      </section>

      <section className="security-panel">
        <div className="security-panel-heading">
          <div>
            <h2>Employees</h2>
            <p>{users.length} account{users.length === 1 ? '' : 's'} · Employee IDs bind workflow decisions to the signed-in user.</p>
          </div>
          <button className="security-refresh" onClick={() => void reload()} aria-label="Refresh employees"><RefreshCw size={15} /></button>
        </div>
        {loading ? <p className="security-empty">Loading employee accounts...</p> : (
          <div className="security-table-wrap">
            <table className="security-table">
              <thead><tr><th>Employee</th><th>Role</th><th>Status</th><th>Access</th><th>Password</th></tr></thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id}>
                    <td><strong>{user.name}</strong><span>{user.email} · {user.employeeId}</span></td>
                    <td>
                      <select
                        aria-label={`Role for ${user.name}`}
                        value={user.role}
                        disabled={!user.active || user.id === currentEmployee.id}
                        onChange={(event) => void changeUser(user, { role: event.target.value as EmployeeRole })}
                      >
                        {employeeRoles.map((role) => <option key={role}>{role}</option>)}
                      </select>
                    </td>
                    <td><span className={`security-status ${user.active ? 'is-active' : ''}`}>{user.active ? 'Active' : 'Disabled'}</span></td>
                    <td>
                      <button
                        className="security-access-button"
                        disabled={user.id === currentEmployee.id}
                        onClick={() => void changeUser(user, { active: !user.active })}
                      >
                        {user.id === currentEmployee.id ? 'You' : user.active ? 'Disable' : 'Enable'}
                      </button>
                    </td>
                    <td>
                      <form className="security-reset-form" onSubmit={(event) => void resetPassword(event, user)}>
                        <input
                          aria-label={`New password for ${user.name}`}
                          type="password"
                          autoComplete="new-password"
                          minLength={12}
                          maxLength={1024}
                          required
                          disabled={!user.active}
                          value={resetPasswords[user.id] ?? ''}
                          onChange={(event) => setResetPasswords((current) => ({ ...current, [user.id]: event.target.value }))}
                        />
                        <button className="security-access-button" type="submit" disabled={!user.active}>Reset</button>
                      </form>
                    </td>
                  </tr>
                ))}
                {!users.length && <tr><td colSpan={5}>No employee accounts found.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

export function AuditLog() {
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const reload = async () => {
    setError('')
    try {
      setEvents(await fetchAuditEvents())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load audit events.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let active = true
    void fetchAuditEvents()
      .then((loadedEvents) => {
        if (active) setEvents(loadedEvents)
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Unable to load audit events.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  return (
    <div className="security-content">
      <div className="page-heading">
        <div>
          <p>READ-ONLY RECORDS</p>
          <h1>Audit log</h1>
          <span>Employee sign-ins, data access, workflow changes, and denied access attempts.</span>
        </div>
        <button className="security-refresh security-refresh-labeled" onClick={() => void reload()}><RefreshCw size={15} /> Refresh</button>
      </div>
      {error && <div className="security-message security-message-error" role="alert">{error}</div>}
      <section className="security-panel">
        <div className="security-panel-heading">
          <div><h2>Recent activity</h2><p>{events.length} most recent recorded events</p></div>
        </div>
        {loading ? <p className="security-empty">Loading audit events...</p> : (
          <div className="security-table-wrap">
            <table className="security-table audit-table">
              <thead><tr><th>Time</th><th>Employee</th><th>Event</th><th>Resource</th><th>Details</th><th>Source</th></tr></thead>
              <tbody>
                {events.map((event) => (
                  <tr key={event.id}>
                    <td>{new Date(`${event.createdAt.replace(' ', 'T')}Z`).toLocaleString()}</td>
                    <td>{event.actor ?? 'Unknown / invalid sign-in'}</td>
                    <td><code>{event.event}</code></td>
                    <td>{event.resource}</td>
                    <td>{event.details || '—'}</td>
                    <td>{event.ipAddress || '—'}</td>
                  </tr>
                ))}
                {!events.length && <tr><td colSpan={6}>No audit events have been recorded.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

export function HistoricalRecords() {
  const [records, setRecords] = useState<HistoricalRecord[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    void fetchHistoricalRecords()
      .then((loadedRecords) => {
        if (active) setRecords(loadedRecords)
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Unable to load historical records.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  return (
    <div className="security-content">
      <div className="page-heading">
        <div>
          <p>READ-ONLY RECORDS</p>
          <h1>Historical applications</h1>
          <span>Historical deal and repayment outcomes. Personal identifiers and addresses are omitted.</span>
        </div>
      </div>
      {error && <div className="security-message security-message-error" role="alert">{error}</div>}
      <section className="security-panel">
        <div className="security-panel-heading">
          <div><h2>Historical portfolio</h2><p>{records.length} read-only records</p></div>
        </div>
        {loading ? <p className="security-empty">Loading historical records...</p> : (
          <div className="security-table-wrap">
            <table className="security-table audit-table">
              <thead><tr><th>Deal</th><th>Date</th><th>Business</th><th>Industry</th><th>Amount</th><th>Product</th><th>Status</th><th>Repayment</th></tr></thead>
              <tbody>
                {records.map((record) => (
                  <tr key={record.dealId}>
                    <td>{record.dealId}</td>
                    <td>{record.applicationDate}</td>
                    <td>{record.business}</td>
                    <td>{record.industry}</td>
                    <td>{record.amount ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(Number(record.amount)) : '—'}</td>
                    <td>{record.product}</td>
                    <td>{record.status}</td>
                    <td>{record.repaymentStatus}</td>
                  </tr>
                ))}
                {!records.length && <tr><td colSpan={8}>No historical records found.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
