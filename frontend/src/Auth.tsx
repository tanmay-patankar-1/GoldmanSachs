import { useState } from 'react'
import type { FormEvent } from 'react'
import { ShieldCheck } from 'lucide-react'
import { login } from './data/authApi'
import type { Employee } from './data/authApi'
import './Auth.css'

export default function Auth({
  onAuthenticated,
  initialMessage = '',
}: {
  onAuthenticated: (employee: Employee) => void
  initialMessage?: string
}) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(initialMessage)
  const [loading, setLoading] = useState(false)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    setLoading(true)
    try {
      onAuthenticated(await login(email, password))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to sign in.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-brand-mark">GS</div>
        <p className="auth-kicker">COMMERCIAL LENDING</p>
        <h1 id="auth-title">Sign in to DealPilot</h1>
        <p className="auth-intro">Use your company account to access your lending workspace.</p>
        <form className="auth-form" onSubmit={(event) => void submit(event)}>
          <label htmlFor="employee-email">Company email</label>
          <input
            id="employee-email"
            type="email"
            autoComplete="username"
            maxLength={320}
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <label htmlFor="employee-password">Password</label>
          <input
            id="employee-password"
            type="password"
            autoComplete="current-password"
            maxLength={1024}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {error && <p className="auth-error" role="alert">{error}</p>}
          <button className="button button-primary auth-submit" type="submit" disabled={loading}>
            {loading ? 'Signing in...' : 'Sign in'}
          </button>
        </form>
        <div className="auth-security-note">
          <ShieldCheck size={16} />
          <span>Protected employee access · Sign-in attempts are recorded</span>
        </div>
      </section>
    </main>
  )
}
