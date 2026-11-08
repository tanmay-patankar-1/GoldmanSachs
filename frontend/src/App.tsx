import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  ArrowUpRight,
  Bell,
  BriefcaseBusiness,
  CalendarClock,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Download,
  FileCheck2,
  FileText,
  Fingerprint,
  GitBranch,
  HandCoins,
  LayoutDashboard,
  Link2,
  ListFilter,
  Menu,
  MoreHorizontal,
  Network,
  LogOut,
  Search,
  Send,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Users,
  X,
} from 'lucide-react'
import {
  activityItems,
  historicalReference,
  loans as initialLoans,
  navItems,
  stages,
  type Loan,
  type RiskLevel,
  type Stage,
} from './data/mockData'
import {
  advanceWorkflowClock,
  fetchWorkflow,
  fetchFraudAnalysis,
  postWorkflowAction,
  recordApplicationAccess,
  downloadApplicationDocument,
  type CrossDealSignal,
  type FraudAnalysis,
  type WorkflowSnapshot,
} from './data/fraudApi'
import Auth from './Auth'
import { AuditLog, HistoricalRecords, UserManagement } from './Security'
import { getCurrentEmployee, logout } from './data/authApi'
import type { Employee } from './data/authApi'
import './App.css'

type ViewId = (typeof navItems)[number]['id'] | 'audit' | 'history' | 'users'
type DetailTab = 'Overview' | 'Documents' | 'Activity'
type Signal = CrossDealSignal & {
  loanIds: string[]
  reviewIndex: number | null
}
type ActionForm = { note: string; product?: string; pricing?: string }
type PendingAction = { loan: Loan; actionLabel: string; action: string }

const money = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
})

function exportApplications(rows: Loan[]) {
  const headers = [
    'Application ID',
    'Business',
    'Legal name',
    'Requested amount',
    'Product',
    'Stage',
    'Risk level',
    'Risk score',
    'Relationship manager',
  ]
  const values = rows.map((loan) => [
    loan.id,
    loan.name,
    loan.legalName,
    loan.amount,
    loan.product,
    loan.stage,
    loan.risk,
    loan.riskScore,
    loan.relationshipManager,
  ])
  const csv = [headers, ...values]
    .map((row) =>
      row
        .map((value) => `"${String(value).replaceAll('"', '""')}"`)
        .join(','),
    )
    .join('\r\n')
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'dealpilot-applications.csv'
  anchor.click()
  URL.revokeObjectURL(url)
}

function normalizeBorrowerName(value: string) {
  return value
    .toLowerCase()
    .replace(/\b(llc|incorporated|inc|limited|ltd|pllc|llp|company|co|corp|corporation)\b/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

function findLoanForBorrower(name: string, loans: Loan[]) {
  const target = normalizeBorrowerName(name)
  return loans.find((loan) =>
    [loan.name, loan.legalName].some((candidate) => {
      const normalized = normalizeBorrowerName(candidate)
      return normalized === target || normalized.includes(target) || target.includes(normalized)
    }),
  )
}

function findAnalysisScore(
  scores: FraudAnalysis['scores'],
  borrowerNames: string[],
  folder?: string,
) {
  const folderMatch = folder
    ? scores.find((score) => score.folder === folder)
    : undefined
  if (folderMatch) return folderMatch

  const normalizedNames = borrowerNames
    .map(normalizeBorrowerName)
    .filter(Boolean)
  const exactMatch = scores.find((score) =>
    normalizedNames.includes(normalizeBorrowerName(score.borrower)),
  )
  if (exactMatch) return exactMatch

  const candidates = scores.filter((score) => {
    const borrower = normalizeBorrowerName(score.borrower)
    return normalizedNames.some((name) =>
      borrower.includes(name) || name.includes(borrower),
    )
  })
  return candidates.length === 1 ? candidates[0] : undefined
}

function getApplicationValue(
  application: NonNullable<Loan['workflow']>['application'],
  key: string,
) {
  return application[key]?.value ?? ''
}

const riskTone: Record<RiskLevel, string> = {
  Low: 'low',
  Moderate: 'moderate',
  Elevated: 'elevated',
  High: 'high',
  'No scored lead': 'unassessed',
  'Not assessed': 'unassessed',
}

function App() {
  const [employee, setEmployee] = useState<Employee | null>(null)
  const [sessionLoading, setSessionLoading] = useState(true)
  const [sessionError, setSessionError] = useState('')

  useEffect(() => {
    let active = true
    const expireSession = () => {
      if (active) setEmployee(null)
    }
    window.addEventListener('dealpilot:unauthorized', expireSession)
    void getCurrentEmployee()
      .then((user) => {
        if (active) setEmployee(user)
      })
      .catch((error: unknown) => {
        if (active) setSessionError(error instanceof Error ? error.message : 'Unable to verify your session.')
      })
      .finally(() => {
        if (active) setSessionLoading(false)
      })
    return () => {
      active = false
      window.removeEventListener('dealpilot:unauthorized', expireSession)
    }
  }, [])

  useEffect(() => {
    if (!employee) return
    const timer = window.setInterval(() => {
      void getCurrentEmployee()
        .then((current) => {
          if (!current || current.id !== employee.id || current.role !== employee.role) {
            setEmployee(null)
          }
        })
        .catch((error: unknown) => {
          setSessionError(error instanceof Error ? error.message : 'Session verification failed.')
          setEmployee(null)
        })
    }, 60_000)
    return () => window.clearInterval(timer)
  }, [employee])

  const signOut = async () => {
    try {
      await logout()
      setEmployee(null)
    } catch (error) {
      setSessionError(error instanceof Error ? error.message : 'Unable to sign out.')
    }
  }

  if (sessionLoading) {
    return <main className="auth-page"><p role="status">Checking your Verifin session...</p></main>
  }
  if (!employee) {
    return <Auth onAuthenticated={setEmployee} initialMessage={sessionError} />
  }
  return <DealPilotApp employee={employee} onLogout={() => void signOut()} />
}

function DealPilotApp({
  employee,
  onLogout,
}: {
  employee: Employee
  onLogout: () => void
}) {
  const [view, setView] = useState<ViewId>(
    employee.role === 'Auditor' ? 'audit' :
      employee.role === 'Fraud Investigator' ? 'fraud' : 'overview',
  )
  const [selectedLoanId, setSelectedLoanId] = useState<string | null>(null)
  const [selectedSignalId, setSelectedSignalId] = useState('')
  const [search, setSearch] = useState('')
  const [stageFilter, setStageFilter] = useState('All stages')
  const [riskFilter, setRiskFilter] = useState('All risk levels')
  const [toast, setToast] = useState('')
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [networkFocusId, setNetworkFocusId] = useState('DP-26019')
  const [fraudAnalysis, setFraudAnalysis] = useState<FraudAnalysis | null>(null)
  const [analysisError, setAnalysisError] = useState('')
  const [analysisLoading, setAnalysisLoading] = useState(
    employee.role === 'Admin' || employee.role === 'Fraud Investigator',
  )
  const [analysisRefreshing, setAnalysisRefreshing] = useState(false)
  const [workflow, setWorkflow] = useState<WorkflowSnapshot | null>(null)
  const [workflowError, setWorkflowError] = useState('')
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null)
  const cancelPendingAction = useCallback(() => setPendingAction(null), [])

  const loans = useMemo(() => {
    if (!workflow) return initialLoans
    const accents = ['blue', 'violet', 'amber', 'teal', 'orange']
    return workflow.loans.map((item, index): Loan => {
      const application = item.application
      const borrowerScoreRecord = fraudAnalysis
        ? findAnalysisScore(
            fraudAnalysis.scores,
            [item.borrower, application.legalName?.value ?? ''],
            item.folder,
          )
        : undefined
      const fullName = application.legalName?.value
        || (item.borrower === item.folder ? borrowerScoreRecord?.borrower : undefined)
        || item.borrower
      const borrowerAliases = new Set([
        normalizeBorrowerName(fullName),
        normalizeBorrowerName(borrowerScoreRecord?.borrower ?? ''),
      ])
      const flaggedChecks = item.checks.filter((check) => check.status === 'flagged')
      const relatedSignals = fraudAnalysis?.signals.filter((signal) =>
        signal.borrowers.some(
          (borrower) => borrowerAliases.has(normalizeBorrowerName(borrower)),
        ),
      ) ?? []
      const riskScore = borrowerScoreRecord?.score ?? null
      const requirements = item.requiredDocuments
      const missingDocuments = requirements
        .filter((document) => document.status !== 'received')
        .map((document) => document.label)
      const incompleteInputs = missingDocuments.length > 0
        || item.checks.length !== 5
        || item.checks.some((check) => check.status === 'not_evaluable')
      const risk: RiskLevel =
        riskScore === null ? 'Not assessed' :
          riskScore === 0 && incompleteInputs ? 'Not assessed' :
            riskScore === 0 ? 'No scored lead' :
            riskScore >= 76 ? 'High' :
              riskScore >= 51 ? 'Elevated' :
                riskScore >= 26 ? 'Moderate' : 'Low'
      const requestedAmount = application.amountRaw?.numericValue ?? 0
      const revenue = application.revenueRaw?.numericValue ?? 0
      const term = application.termRaw?.numericValue ?? 0
      const displayName = fullName.trim()
      const initials = fullName
        .split(/\s+/)
        .slice(0, 2)
        .map((part) => part[0] ?? '')
        .join('')
        .toUpperCase()
      const nextAction =
        item.blockers[0] ??
        (['Credit', 'Committee', 'Committed', 'Closed'].includes(item.stage)
          ? `${item.stage} human decision`
          : `Review ${item.stage.toLowerCase()} requirements`)
      const status = item.status.replaceAll('_', ' ')
      return {
        id: item.id,
        name: displayName,
        legalName: application.legalName?.value ?? displayName,
        industry: getApplicationValue(application, 'industry') || 'Not extracted',
        amount: requestedAmount,
        product: getApplicationValue(application, 'product') || 'Not extracted',
        purpose: getApplicationValue(application, 'purpose') || 'Not extracted',
        term,
        stage: item.stage as Stage,
        risk,
        riskScore,
        knownRuleFlags: flaggedChecks.map((check) => check.code),
        status: status.charAt(0).toUpperCase() + status.slice(1),
        relationshipManager: item.relationshipManager || 'Unassigned',
        creditOfficer: item.creditOfficer || 'Unassigned',
        lastUpdated: 'Current workflow',
        nextAction,
        documents: {
          received: requirements.filter((document) => document.status === 'received').length,
          required: requirements.length,
        },
        missingDocuments,
        flags: [
          ...flaggedChecks.map((check) => check.code),
          ...relatedSignals.map((signal) => signal.title),
        ],
        revenue,
        initials: initials || 'NA',
        accent: accents[index % accents.length],
        neededBy: getApplicationValue(application, 'fundsNeededBy') || undefined,
        linkedIds: [item.id],
        workflow: item,
      }
    })
  }, [fraudAnalysis, workflow])

  const acceptAnalysis = (result: FraudAnalysis) => {
      setFraudAnalysis(result)
      setAnalysisError('')
      setSelectedSignalId((current) =>
        result.signals.some((signal) => signal.id === current)
          ? current
          : result.signals[0]?.id ?? '',
      )
  }

  const refreshFraudAnalysis = async () => {
    setAnalysisRefreshing(true)
    try {
      acceptAnalysis(await fetchFraudAnalysis(true))
    } catch (error) {
      setAnalysisError(
        error instanceof Error ? error.message : 'Unable to load analysis results.',
      )
      setFraudAnalysis(null)
    } finally {
      setAnalysisRefreshing(false)
    }
  }

  useEffect(() => {
    if (!['Admin', 'Fraud Investigator'].includes(employee.role)) return
    let active = true
    void fetchFraudAnalysis()
      .then((result) => {
        if (active) acceptAnalysis(result)
      })
      .catch((error: unknown) => {
        if (!active) return
        setAnalysisError(
          error instanceof Error ? error.message : 'Unable to load analysis results.',
        )
        setFraudAnalysis(null)
      })
      .finally(() => {
        if (active) setAnalysisLoading(false)
      })
    return () => {
      active = false
    }
  }, [employee.role])

  useEffect(() => {
    if (!['Admin', 'Loan Officer', 'Underwriter', 'Fraud Investigator'].includes(employee.role)) return
    let active = true
    void fetchWorkflow()
      .then((result) => {
        if (!active) return
        setWorkflow(result)
        setWorkflowError('')
      })
      .catch((error: unknown) => {
        if (!active) return
        setWorkflowError(
          error instanceof Error ? error.message : 'Unable to load workflow data.',
        )
      })
    return () => {
      active = false
    }
  }, [employee.role])

  const signals: Signal[] = useMemo(() => {
    if (!fraudAnalysis) return []
    return fraudAnalysis.signals.map((signal) => {
      const loanIds = signal.borrowers
        .map((borrower) => findLoanForBorrower(borrower, loans)?.id)
        .filter((id): id is string => Boolean(id))
      const borrowerScores = signal.borrowers.flatMap((borrower) => {
        const score = fraudAnalysis.scores.find(
          (item) => normalizeBorrowerName(item.borrower) === normalizeBorrowerName(borrower),
        )?.score
        return score === undefined ? [] : [score]
      })
      const reviewIndex = borrowerScores.length ? Math.max(...borrowerScores) : null
      return { ...signal, loanIds, reviewIndex }
    })
  }, [fraudAnalysis, loans])

  const selectedLoan =
    loans.find((loan) => loan.id === selectedLoanId) ?? null
  const selectedSignal =
    signals.find((signal) => signal.id === selectedSignalId) ?? signals[0] ?? null

  const filteredLoans = useMemo(() => {
    const query = search.trim().toLowerCase()
    return loans.filter((loan) => {
      const matchesQuery =
        !query ||
        [loan.name, loan.legalName, loan.id, loan.relationshipManager]
          .join(' ')
          .toLowerCase()
          .includes(query)
      const matchesStage =
        stageFilter === 'All stages' || loan.stage === stageFilter
      const matchesRisk =
        riskFilter === 'All risk levels' || loan.risk === riskFilter
      return matchesQuery && matchesStage && matchesRisk
    })
  }, [loans, riskFilter, search, stageFilter])

  const notify = (message: string) => {
    setToast(message)
    window.setTimeout(() => setToast(''), 3200)
  }

  const handleAction = async (loan: Loan, actionLabel: string, form?: ActionForm) => {
    const item = loan.workflow
    if (!item) {
      notify('Connect to the workflow service before recording a decision.')
      return
    }
    let action = ({
      Approve: 'approve',
      'Request information': 'request_info',
      'Request fraud hold': 'request_hold',
      Escalate: 'escalate',
      'Mark reviewed': 'mark_reviewed',
    } as Record<string, string>)[actionLabel] ?? actionLabel
    let alertId = ''
    let checkCode = ''
    const alertAcknowledgement = action.match(/^acknowledge_alert:(\d+):[^:]+$/)
    if (alertAcknowledgement) {
      action = 'acknowledge_alert'
      alertId = alertAcknowledgement[1]
    } else {
      const alertIdOnly = action.match(/^acknowledge_alert:(\d+)$/)
      if (alertIdOnly) {
        action = 'acknowledge_alert'
        alertId = alertIdOnly[1]
      }
    }
    const checkResolution = action.match(/^resolve_check:([^:]+)$/)
    if (checkResolution) {
      action = 'resolve_check'
      checkCode = checkResolution[1]
    }
    let note = form?.note ?? ''
    let product = form?.product ?? ''
    let pricing = form?.pricing ?? ''
    if (['approve', 'decline', 'request_info', 'request_hold', 'escalate', 'place_hold', 'release_hold', 'clear_fraud', 'verify_vendor', 'verify_disbursement', 'resolve_check', 'release_funds'].includes(action)) {
      if (!form) {
        setPendingAction({ loan, actionLabel, action })
        return
      }
      note = note.trim()
      if (!note) {
        notify('A rationale is required for this workflow action.')
        return
      }
    } else if (action === 'configure_product') {
      if (!form) {
        setPendingAction({ loan, actionLabel, action })
        return
      }
      product = product.trim()
      pricing = pricing.trim()
      if (!product || !pricing) {
        notify('Enter both the loan product and pricing details.')
        return
      }
    }
    try {
      const payload: Record<string, string> = { action }
      if (note) payload.note = note
      if (product) payload.product = product
      if (pricing) payload.pricing = pricing
      if (alertId) payload.alertId = alertId
      if (checkCode) payload.checkCode = checkCode
      setWorkflow(await postWorkflowAction(item.id, payload))
      setWorkflowError('')
      notify(`${actionLabel} recorded for ${loan.name}.`)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Workflow action failed.'
      setWorkflowError(message)
      notify(message)
    }
  }

  const advanceClock = async () => {
    try {
      setWorkflow(await advanceWorkflowClock('1h'))
      setWorkflowError('')
      notify('Simulation clock advanced by one hour.')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to advance simulation time.'
      setWorkflowError(message)
      notify(message)
    }
  }

  const simulationDate = workflow
    ? new Intl.DateTimeFormat('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        timeZone: 'America/Chicago',
      }).format(new Date(workflow.now))
    : 'Workflow offline'
  const simulationTime = workflow
    ? new Intl.DateTimeFormat('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        timeZone: 'America/Chicago',
        timeZoneName: 'short',
      }).format(new Date(workflow.now))
    : '—'

  const navigate = (nextView: ViewId) => {
    setView(nextView)
    setMobileNavOpen(false)
    setSelectedLoanId(null)
  }

  const canAccessLoans = ['Admin', 'Loan Officer', 'Underwriter'].includes(employee.role)
  const canAccessFraud = ['Admin', 'Fraud Investigator'].includes(employee.role)
  const canAccessAudit = ['Admin', 'Auditor'].includes(employee.role)
  const canManageUsers = employee.role === 'Admin'
  const viewLabel = view === 'audit'
    ? 'Audit log'
    : view === 'history'
      ? 'Historical applications'
      : view === 'users'
        ? 'Employee access'
        : navItems.find((item) => item.id === view)?.label ?? 'Overview'
  const visibleNavItems = navItems.filter((item) =>
    item.id === 'overview'
      ? ['Admin', 'Loan Officer', 'Underwriter'].includes(employee.role)
      : item.id === 'applications' || item.id === 'workflow'
        ? canAccessLoans
        : canAccessFraud,
  )

  const openLoan = async (id: string) => {
    if (!canAccessLoans) {
      notify('Your role does not have access to application details.')
      return
    }
    try {
      await recordApplicationAccess(id)
      setSelectedLoanId(id)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to open this application.'
      setWorkflowError(message)
      notify(message)
    }
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNavOpen ? 'sidebar-open' : ''}`}>
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <ArrowUpRight size={20} strokeWidth={2.4} />
          </div>
          <div className="brand-name">
            <strong>Verifin</strong>
            <span>Verify smarter. Financially safer.</span>
          </div>
          <button
            className="icon-button mobile-nav-close"
            aria-label="Close navigation"
            onClick={() => setMobileNavOpen(false)}
          >
            <X size={18} />
          </button>
        </div>

        <div className="workspace-switcher">
          <div className="workspace-icon">
            <BriefcaseBusiness size={15} />
          </div>
          <div className="workspace-copy">
            <span>Workspace</span>
            <strong>Commercial lending</strong>
          </div>
          <ChevronDown size={15} className="muted-icon" />
        </div>

        <nav className="primary-nav" aria-label="Main navigation">
          {(['WORKSPACE', 'REVIEW & FOLLOW-UP'] as const).map((group) => (
              <div className="nav-group" key={group}>
                <p className="nav-group-label">{group}</p>
                {visibleNavItems
                  .filter((item) => item.group === group)
                  .map((item) => {
                    const Icon =
                      item.id === 'overview'
                        ? LayoutDashboard
                        : item.id === 'applications'
                          ? BriefcaseBusiness
                          : item.id === 'fraud'
                            ? ShieldAlert
                            : item.id === 'network'
                              ? Network
                              : Activity
                    return (
                      <button
                        key={item.id}
                        className={`nav-item ${view === item.id ? 'nav-item-active' : ''}`}
                        onClick={() => navigate(item.id)}
                      >
                        <Icon size={17} strokeWidth={1.8} />
                        <span>{item.label}</span>
                      </button>
                    )
                  })                  }
              </div>
          ))}
          {canAccessAudit && (
            <div className="nav-group">
              <p className="nav-group-label">RECORDS</p>
              <button className={`nav-item ${view === 'audit' ? 'nav-item-active' : ''}`} onClick={() => navigate('audit')}>
                  <ShieldCheck size={17} strokeWidth={1.8} /><span>Audit log</span>
              </button>
              <button className={`nav-item ${view === 'history' ? 'nav-item-active' : ''}`} onClick={() => navigate('history')}>
                  <FileText size={17} strokeWidth={1.8} /><span>Historical applications</span>
              </button>
            </div>
          )}
          {canManageUsers && (
            <div className="nav-group">
              <p className="nav-group-label">ADMINISTRATION</p>
              <button className={`nav-item ${view === 'users' ? 'nav-item-active' : ''}`} onClick={() => navigate('users')}>
                  <Users size={17} strokeWidth={1.8} /><span>Employee access</span>
              </button>
            </div>
          )}
        </nav>

        <div className="sidebar-bottom">
          <div className="environment-status">
            <span className="online-dot" />
            <span>CHALLENGE SIMULATION</span>
          </div>
          <div className="profile-switch">
            <div className="avatar avatar-navy">{employee.name.split(/\s+/).slice(0, 2).map((part) => part[0] ?? '').join('').toUpperCase()}</div>
            <div className="profile-copy">
              <strong>{employee.name}</strong>
              <span>{employee.role}</span>
            </div>
            <button className="icon-button" aria-label="Sign out" title="Sign out" onClick={onLogout}><LogOut size={16} /></button>
          </div>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            aria-label="Open navigation"
            onClick={() => setMobileNavOpen(true)}
          >
            <Menu size={20} />
          </button>
          <div className="breadcrumb">
            <span>Commercial lending</span>
            <ChevronRight size={14} />
            <strong>{viewLabel}</strong>
          </div>
          <div className="topbar-actions">
            {employee.role === 'Admin' && <div className="sim-clock">
              <span className={`clock-live-dot ${workflow ? '' : 'clock-offline'}`} />
              <span>Demo time</span>
              <strong>{simulationDate}</strong>
              <span className="clock-divider" />
              <strong>{simulationTime}</strong>
              <button
                className="clock-advance"
                aria-label="Advance simulation clock by one hour"
                title="Advance simulation clock by one hour"
                disabled={!workflow}
                onClick={() => void advanceClock()}
              >
                Next hour
              </button>
            </div>}
            {canAccessLoans && <button
              className="icon-button notification-button"
              aria-label="Notifications"
              onClick={() => navigate('workflow')}
            >
              <Bell size={18} />
              {(workflow?.alerts.some((alert) => !alert.acknowledged) ?? false) && (
                <span className="notification-dot" />
              )}
            </button>}
            <div className="topbar-avatar">{employee.name.split(/\s+/).slice(0, 2).map((part) => part[0] ?? '').join('').toUpperCase()}</div>
          </div>
        </header>

        <div className="page-content">
          {workflowError && (
            <div className="workflow-error-banner" role="alert">
              <AlertTriangle size={15} />
              <span>Live workflow unavailable. This view may contain demonstration data. {workflowError}</span>
            </div>
          )}
          {view === 'overview' && canAccessLoans && (
            <Dashboard
              loans={loans}
              workflow={workflow}
              onOpenLoan={(id) => void openLoan(id)}
              onNavigate={navigate}
            />
          )}
          {view === 'applications' && (
            <Applications
              loans={filteredLoans}
              allCount={loans.length}
              search={search}
              setSearch={setSearch}
              stageFilter={stageFilter}
              setStageFilter={setStageFilter}
              riskFilter={riskFilter}
              setRiskFilter={setRiskFilter}
              onOpenLoan={(id) => void openLoan(id)}
            />
          )}
          {view === 'fraud' && canAccessFraud && (
            <FraudCenter
              signals={signals}
              selectedSignal={selectedSignal}
              setSelectedSignalId={setSelectedSignalId}
              loans={loans}
              analysis={fraudAnalysis}
              analysisError={analysisError}
              analysisLoading={analysisLoading}
              analysisRefreshing={analysisRefreshing}
              onRefresh={() => void refreshFraudAnalysis()}
              onOpenLoan={(id) => void openLoan(id)}
              onOpenNetwork={(id) => {
                setNetworkFocusId(id)
                navigate('network')
              }}
              onAction={handleAction}
            />
          )}
          {view === 'network' && canAccessFraud && (
            <NetworkExplorer
              loans={loans}
              initialSelection={networkFocusId}
              analysis={fraudAnalysis}
              onOpenLoan={(id) => void openLoan(id)}
            />
          )}
          {view === 'workflow' && canAccessLoans && (
            <WorkflowMonitor
              loans={loans}
              workflow={workflow}
              onOpenLoan={(id) => void openLoan(id)}
              onNavigate={navigate}
              onAction={(loan, action) => void handleAction(loan, action)}
            />
          )}
          {view === 'audit' && canAccessAudit && <AuditLog />}
          {view === 'history' && canAccessAudit && <HistoricalRecords />}
          {view === 'users' && canManageUsers && <UserManagement currentEmployee={employee} onLogout={onLogout} />}
        </div>
      </main>

      {selectedLoan && (
        <LoanDrawer
          loan={selectedLoan}
          onClose={() => setSelectedLoanId(null)}
          onAction={(action) => handleAction(selectedLoan, action)}
          onDownload={(filename) => downloadApplicationDocument(selectedLoan.id, filename)}
          canDownload={employee.role === 'Admin' || employee.role === 'Underwriter'}
        />
      )}
      {pendingAction && (
        <ActionDialog
          loan={pendingAction.loan}
          action={pendingAction.action}
          actionLabel={pendingAction.actionLabel}
          onCancel={cancelPendingAction}
          onSubmit={(form) => {
            setPendingAction(null)
            void handleAction(pendingAction.loan, pendingAction.actionLabel, form)
          }}
        />
      )}
      {mobileNavOpen && (
        <button
          className="mobile-backdrop"
          aria-label="Close navigation"
          onClick={() => setMobileNavOpen(false)}
        />
      )}
      {toast && (
        <div className="toast-message" role="status">
          <CheckCircle2 size={17} />
          {toast}
        </div>
      )}
    </div>
  )
}

function PageHeading({
  eyebrow,
  title,
  subtitle,
  children,
}: {
  eyebrow: string
  title: string
  subtitle: string
  children?: ReactNode
}) {
  return (
    <div className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="page-subtitle">{subtitle}</p>
      </div>
      {children && <div className="heading-actions">{children}</div>}
    </div>
  )
}

function ActionDialog({
  loan,
  action,
  actionLabel,
  onCancel,
  onSubmit,
}: {
  loan: Loan
  action: string
  actionLabel: string
  onCancel: () => void
  onSubmit: (form: ActionForm) => void
}) {
  const isProductSetup = action === 'configure_product'
  const [note, setNote] = useState('')
  const [product, setProduct] = useState(
    getApplicationValue(loan.workflow?.application ?? {}, 'product'),
  )
  const [pricing, setPricing] = useState(
    getApplicationValue(loan.workflow?.application ?? {}, 'term') ||
      `${loan.term} month term`,
  )
  const copy: Record<string, { title: string; description: string }> = {
    approve: {
      title: 'Approve this application',
      description: 'Explain the decision. This records your approval and advances the application.',
    },
    decline: {
      title: 'Decline this application',
      description: 'Record the reason for the decision.',
    },
    request_info: {
      title: 'Request information',
      description: 'List what is missing. This records the request; it does not send a message automatically.',
    },
    request_hold: {
      title: 'Request a fraud review',
      description: 'Describe what should be reviewed. This asks Fraud Operations to consider a hold; it does not place one.',
    },
    escalate: {
      title: 'Escalate this application',
      description: 'Explain why another reviewer should take a look.',
    },
    place_hold: {
      title: 'Place a funding hold',
      description: 'A hold pauses funding while the concern is reviewed. Record the evidence and reason.',
    },
    release_hold: {
      title: 'Release the funding hold',
      description: 'Record why it is appropriate to continue.',
    },
    clear_fraud: {
      title: 'Complete the fraud review',
      description: 'Record the evidence supporting the final review decision.',
    },
    verify_vendor: {
      title: 'Record vendor verification',
      description: 'Describe how the vendor and payment details were checked independently.',
    },
    verify_disbursement: {
      title: 'Verify the payment account',
      description: 'Describe how the account receiving the funds was checked independently.',
    },
    resolve_check: {
      title: 'Record your review',
      description: 'Add your review notes. This does not change the automated check result.',
    },
    release_funds: {
      title: 'Authorize funding',
      description: 'Record why it is appropriate to release the funds.',
    },
    configure_product: {
      title: 'Confirm loan details',
      description: 'Check the loan product and pricing before saving them.',
    },
  }
  const actionCopy = copy[action] ?? {
    title: actionLabel,
    description: 'Add a note explaining this action.',
  }

  useEffect(() => {
    const previousFocus = document.activeElement
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      if (previousFocus instanceof HTMLElement) previousFocus.focus()
    }
  }, [onCancel])

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    onSubmit({ note: note.trim(), product: product.trim(), pricing: pricing.trim() })
  }

  return (
    <div
      className="action-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCancel()
      }}
    >
      <section
        className="action-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="action-dialog-title"
        aria-describedby="action-dialog-description"
      >
        <div className="action-dialog-heading">
          <div>
            <p className="eyebrow">{loan.name} · {loan.id}</p>
            <h2 id="action-dialog-title">{actionCopy.title}</h2>
          </div>
          <button className="icon-button" aria-label="Close" onClick={onCancel}><X size={19} /></button>
        </div>
        <p id="action-dialog-description" className="action-dialog-description">{actionCopy.description}</p>
        <form onSubmit={submit}>
          {isProductSetup ? (
            <div className="action-dialog-fields">
              <label>
                Loan product
                <input autoFocus required value={product} onChange={(event) => setProduct(event.target.value)} />
              </label>
              <label>
                Pricing and term
                <input required value={pricing} onChange={(event) => setPricing(event.target.value)} />
              </label>
            </div>
          ) : (
            <label>
              Your notes <span className="required-label">(required)</span>
              <textarea
                autoFocus
                required
                rows={4}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="Write a short explanation..."
              />
            </label>
          )}
          <div className="action-dialog-footer">
            <button type="button" className="button button-secondary" onClick={onCancel}>Cancel</button>
            <button type="submit" className="button button-primary">
              {isProductSetup ? 'Save loan details' : 'Record action'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}

function Dashboard({
  loans,
  workflow,
  onOpenLoan,
  onNavigate,
}: {
  loans: Loan[]
  workflow: WorkflowSnapshot | null
  onOpenLoan: (id: string) => void
  onNavigate: (view: ViewId) => void
}) {
  const active = loans.filter((loan) => loan.stage !== 'Closed').length
  const pending = workflow?.pendingDecisions.length ?? loans.filter((loan) =>
    ['Credit', 'Committee', 'Committed'].includes(loan.stage),
  ).length
  const flagged = loans.filter(
    (loan) => loan.flags.length > 0 || loan.risk === 'High' || loan.risk === 'Elevated',
  ).length
  const overdue = workflow?.alerts.filter((alert) => !alert.acknowledged).length ?? 0
  const attention = loans.filter(
    (loan) =>
      loan.missingDocuments.length > 0 ||
      loan.risk === 'High' ||
      loan.status.includes('Priority') ||
      Boolean(loan.workflow?.blockers.length),
  )
  const dateLabel = workflow
    ? new Intl.DateTimeFormat('en-US', {
        weekday: 'long',
        month: 'long',
        day: 'numeric',
        year: 'numeric',
        timeZone: 'America/Chicago',
      }).format(new Date(workflow.now)).toUpperCase()
    : 'DEMONSTRATION DATA'

  return (
    <>
      <PageHeading
        eyebrow={dateLabel}
        title="Portfolio overview"
        subtitle="Commercial lending · Current application and review status"
      >
        <button className="button button-secondary" onClick={() => onNavigate('applications')}>
          <BriefcaseBusiness size={16} />
          All applications
        </button>
        <button className="button button-primary" onClick={() => onNavigate('fraud')}>
          <Shield size={16} />
          Review alerts
          <span className="button-count">{flagged}</span>
        </button>
      </PageHeading>

      <div className="demo-banner">
        <span>
          <strong>Challenge simulation</strong>
          <span> Synthetic borrower data · Human decisions and risk signals require review</span>
        </span>
        <span className="demo-banner-right">{workflow ? 'LIVE WORKFLOW' : 'OFFLINE DEMO'} <span>·</span> {loans.length} APPLICATIONS</span>
      </div>

      <section className="getting-started" aria-labelledby="getting-started-title">
        <div className="getting-started-heading">
          <div>
            <p className="eyebrow">A SIMPLE PLACE TO START</p>
            <h2 id="getting-started-title">What would you like to do?</h2>
            <p>Open a loan, review anything that needs attention, or see where work is waiting.</p>
          </div>
        </div>
        <div className="getting-started-actions">
          <button onClick={() => onNavigate('applications')}>
            <span className="guide-number">1</span>
            <span><strong>Find an application</strong><small>Search the list of businesses and loans</small></span>
            <ChevronRight size={18} />
          </button>
          <button onClick={() => onNavigate('fraud')}>
            <span className="guide-number">2</span>
            <span><strong>Review alerts</strong><small>See items that may need a closer look</small></span>
            <ChevronRight size={18} />
          </button>
          <button onClick={() => onNavigate('workflow')}>
            <span className="guide-number">3</span>
            <span><strong>Check loan progress</strong><small>See what is waiting and who should act</small></span>
            <ChevronRight size={18} />
          </button>
        </div>
      </section>

      <section className="metric-grid" aria-label="Portfolio overview">
        <MetricCard
          label="Active applications"
          value={String(active)}
          note="Across all stages"
          delta="+3"
          tone="blue"
        />
        <MetricCard
          label="Waiting for a decision"
          value={String(pending)}
          note="Awaiting a decision"
          delta="2 need attention"
          tone="blue"
        />
        <MetricCard
          label="Items to review"
          value={String(flagged)}
          note="Alerts are leads, not proof of fraud"
          delta="Needs a person's review"
          tone="rose"
        />
        <MetricCard
          label="Late tasks"
          value={String(overdue)}
          note="Based on the demo clock"
          delta={overdue ? 'Open loan progress' : 'No late tasks'}
          tone="amber"
        />
      </section>

      <section className="dashboard-grid">
        <div className="panel activity-panel">
          <div className="panel-heading">
            <div>
              <h2>Application activity</h2>
              <p>Intake volume and completed decisions</p>
            </div>
            <span className="select-like" aria-label="Selected time period">
              This week <ChevronDown size={14} />
            </span>
          </div>
          <ActivityChart />
        </div>
        <div className="panel stage-panel">
          <div className="panel-heading">
            <div>
              <h2>Pipeline snapshot</h2>
              <p>Applications by current stage</p>
            </div>
            <button
              className="icon-button subtle-icon"
              aria-label="View workflow"
              onClick={() => onNavigate('workflow')}
            >
              <ArrowRight size={17} />
            </button>
          </div>
          <div className="pipeline-bars">
            {stages.map((stage, index) => {
              const count = loans.filter((loan) => loan.stage === stage).length
              const width = Math.max(12, (count / loans.length) * 100)
              return (
                <div className="pipeline-row" key={stage}>
                  <span className="pipeline-label">{stage}</span>
                  <div className="pipeline-track">
                    <span
                      className={`pipeline-fill pipeline-${index}`}
                      style={{ width: `${width}%` }}
                    />
                  </div>
                  <strong>{count}</strong>
                </div>
              )
            })}
          </div>
          <div className="pipeline-footer">
            <span><span className="legend-dot blue-dot" /> In progress</span>
            <span><span className="legend-dot green-dot" /> Completed</span>
          </div>
        </div>
      </section>

      <section className="lower-grid">
        <div className="panel attention-panel">
          <div className="panel-heading">
            <div>
              <h2>Needs your attention</h2>
              <p>Priority items across the portfolio</p>
            </div>
            <button className="text-link" onClick={() => onNavigate('applications')}>
              View all <ArrowRight size={14} />
            </button>
          </div>
          <div className="attention-list">
            {attention.slice(0, 4).map((loan) => (
              <button
                className="attention-row"
                key={loan.id}
                onClick={() => onOpenLoan(loan.id)}
              >
                <div className={`attention-icon ${riskTone[loan.risk]}`}>
                  {loan.risk === 'High' ? (
                    <ShieldAlert size={16} />
                  ) : loan.missingDocuments.length > 0 ? (
                    <FileText size={16} />
                  ) : (
                    <CalendarClock size={16} />
                  )}
                </div>
                <div className="attention-copy">
                  <strong>{loan.name}</strong>
                  <span>{loan.flags[0] ?? loan.nextAction}</span>
                </div>
                <span className={`risk-pill risk-${riskTone[loan.risk]}`}>
                  {loan.risk}
                </span>
                <ChevronRight size={16} className="row-chevron" />
              </button>
            ))}
          </div>
        </div>
        <div className="panel recent-panel">
          <div className="panel-heading">
            <div>
              <h2>Recent activity</h2>
              <p>Latest updates from your team</p>
            </div>
            <button
              className="icon-button subtle-icon"
              aria-label="View all activity"
              onClick={() => onNavigate('workflow')}
            >
              <ArrowRight size={17} />
            </button>
          </div>
          <div className="recent-list">
            {activityItems.map((item) => (
              <div className="recent-row" key={item.title}>
                <span className={`activity-dot ${item.tone}`} />
                <div className="recent-copy">
                  <strong>{item.title}</strong>
                  <span>{item.detail}</span>
                </div>
                <time>{item.time}</time>
              </div>
            ))}
          </div>
          <button className="full-width-link" onClick={() => onNavigate('workflow')}>
            See loan progress <ArrowRight size={14} />
          </button>
        </div>
      </section>
    </>
  )
}

function MetricCard({
  label,
  value,
  note,
  delta,
  tone,
}: {
  label: string
  value: string
  note: string
  delta: string
  tone: string
}) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className="metric-bottom">
        <strong className="metric-value">{value}</strong>
        <span>{note}</span>
        <span className={`metric-delta ${tone}`}>{delta}</span>
      </div>
    </div>
  )
}

function ActivityChart() {
  const values = [22, 35, 29, 49, 40, 62, 54, 72, 49, 67, 60, 82]
  const points = values
    .map((value, index) => {
      const x = 26 + index * 54
      const y = 160 - value * 1.48
      return `${index === 0 ? 'M' : 'L'} ${x} ${y}`
    })
    .join(' ')
  const areaPath = `${points} L ${26 + (values.length - 1) * 54} 168 L 26 168 Z`

  return (
    <div className="chart-wrap">
      <div className="chart-legend">
        <span><i className="chart-key navy-key" /> Applications received <strong>38</strong></span>
        <span><i className="chart-key gold-key" /> Decisions completed <strong>24</strong></span>
      </div>
      <svg
        className="activity-chart"
        viewBox="0 0 640 190"
        role="img"
        aria-label="Illustrative line chart of weekly application activity"
      >
        {[30, 72, 114, 156].map((y) => (
          <line key={y} x1="25" x2="615" y1={y} y2={y} className="chart-gridline" />
        ))}
        <path d={areaPath} className="chart-area" />
        <path d={points} className="chart-line" />
        <path
          d="M 26 139 C 80 136, 105 145, 134 132 S 192 137, 242 123 S 300 130, 350 115 S 405 121, 458 101 S 525 110, 620 88"
          className="chart-line chart-line-gold"
        />
        {['Mon', 'Tue', 'Wed', 'Thu', 'Fri'].map((day, index) => (
          <text key={day} x={37 + index * 137} y="187" className="chart-label">
            {day}
          </text>
        ))}
        <circle cx="296" cy="68" r="4" className="chart-point" />
      </svg>
    </div>
  )
}

function Applications({
  loans,
  allCount,
  search,
  setSearch,
  stageFilter,
  setStageFilter,
  riskFilter,
  setRiskFilter,
  onOpenLoan,
}: {
  loans: Loan[]
  allCount: number
  search: string
  setSearch: (value: string) => void
  stageFilter: string
  setStageFilter: (value: string) => void
  riskFilter: string
  setRiskFilter: (value: string) => void
  onOpenLoan: (id: string) => void
}) {
  return (
    <>
      <PageHeading
        eyebrow="PORTFOLIO"
        title="Applications"
        subtitle="Search and review every loan application in the sample portfolio."
      >
        <button className="button button-secondary" onClick={() => exportApplications(loans)}>
          <ListFilter size={16} />
          Export view
        </button>
        <button className="button button-primary" onClick={() => setSearch('')}>
          <BriefcaseBusiness size={16} />
          {allCount} applications
        </button>
      </PageHeading>

      <div className="table-toolbar">
        <label className="search-field">
          <Search size={17} />
          <input
            aria-label="Search applications"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search borrower, ID, or manager"
          />
          {search && (
            <button aria-label="Clear search" onClick={() => setSearch('')}>
              <X size={15} />
            </button>
          )}
        </label>
        <label className="filter-select">
          <span>Stage</span>
          <select value={stageFilter} onChange={(event) => setStageFilter(event.target.value)}>
            <option>All stages</option>
            {stages.map((stage) => <option key={stage}>{stage}</option>)}
          </select>
          <ChevronDown size={14} />
        </label>
        <label className="filter-select">
          <span>Risk</span>
          <select value={riskFilter} onChange={(event) => setRiskFilter(event.target.value)}>
            <option>All risk levels</option>
            {(['Low', 'Moderate', 'Elevated', 'High', 'No scored lead', 'Not assessed'] as RiskLevel[]).map((risk) => (
              <option key={risk}>{risk}</option>
            ))}
          </select>
          <ChevronDown size={14} />
        </label>
        <div className="results-count">{loans.length} results</div>
      </div>

      <div className="panel applications-table-panel">
        <div className="table-scroll">
          <table className="applications-table">
            <thead>
              <tr>
                <th>BUSINESS / APPLICATION</th>
                <th>REQUESTED</th>
                <th>STAGE</th>
                <th>RISK REVIEW</th>
                <th>DOCUMENTS</th>
                <th>RELATIONSHIP MANAGER</th>
                <th aria-label="Open application" />
              </tr>
            </thead>
            <tbody>
              {loans.map((loan) => (
                <tr
                  key={loan.id}
                  tabIndex={0}
                  onClick={() => onOpenLoan(loan.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onOpenLoan(loan.id)
                    }
                  }}
                >
                  <td>
                    <div className="business-cell">
                      <div className={`business-avatar ${loan.accent}`}>{loan.initials}</div>
                      <div>
                        <strong>{loan.name}</strong>
                        <span>{loan.id} <i /> {loan.industry}</span>
                      </div>
                    </div>
                  </td>
                  <td>
                    <strong className="table-amount">{money.format(loan.amount)}</strong>
                    <span className="table-subtext">{loan.product}</span>
                  </td>
                  <td><StageBadge stage={loan.stage} /></td>
                  <td>
                    <div className="risk-cell">
                      <span className={`risk-pill risk-${riskTone[loan.risk]}`}>{loan.risk}</span>
                      <span className="risk-score">
                        {loan.riskScore === null || loan.risk === 'Not assessed'
                          ? 'Index not assessed'
                          : `${loan.riskScore}/100 observed review index`}
                      </span>
                      {loan.knownRuleFlags?.length ? (
                        <span className="known-rule-indicator">
                          Known rule: {loan.knownRuleFlags.join(', ')}
                        </span>
                      ) : null}
                    </div>
                  </td>
                  <td>
                    <div className="document-cell">
                      <span>{loan.documents.received}/{loan.documents.required}</span>
                      <span className="document-meter">
                        <i style={{ width: `${(loan.documents.received / loan.documents.required) * 100}%` }} />
                      </span>
                      {loan.missingDocuments.length > 0 && (
                        <span className="missing-document-label">Missing item</span>
                      )}
                    </div>
                  </td>
                  <td><span className="manager-name">{loan.relationshipManager}</span></td>
                  <td><ChevronRight size={16} className="row-chevron" /></td>
                </tr>
              ))}
              {loans.length === 0 && (
                <tr>
                  <td colSpan={7}>
                    <div className="empty-state">
                      <Search size={22} />
                      <strong>No applications match those filters</strong>
                      <span>Try a different search term or clear your filters.</span>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="table-footer">
          <span>Showing <strong>{loans.length}</strong> of <strong>{allCount}</strong> applications</span>
          <span>Sample data · Updated just now</span>
        </div>
      </div>
      <p className="data-footnote"><ShieldCheck size={14} /> All borrower and account information shown here is synthetic challenge data.</p>
    </>
  )
}

function StageBadge({ stage }: { stage: Stage }) {
  return (
    <span className={`stage-badge stage-${stage.toLowerCase()}`}>
      <i />
      {stage}
    </span>
  )
}

function FraudCenter({
  signals,
  selectedSignal,
  setSelectedSignalId,
  loans,
  analysis,
  analysisError,
  analysisLoading,
  analysisRefreshing,
  onRefresh,
  onOpenLoan,
  onOpenNetwork,
  onAction,
}: {
  signals: Signal[]
  selectedSignal: Signal | null
  setSelectedSignalId: (id: string) => void
  loans: Loan[]
  analysis: FraudAnalysis | null
  analysisError: string
  analysisLoading: boolean
  analysisRefreshing: boolean
  onRefresh: () => void
  onOpenLoan: (id: string) => void
  onOpenNetwork: (id: string) => void
  onAction: (loan: Loan, action: string) => void
}) {
  const mainLoan = selectedSignal
    ? loans.find((loan) => loan.id === selectedSignal.loanIds[0])
    : undefined
  const historicalMatchIds = [
    ...new Set(signals.flatMap((signal) => signal.historicalDeals.map((deal) => deal.id))),
  ]
  const knownRuleReviews = loans.flatMap((loan) =>
    (loan.workflow?.checks ?? [])
      .filter((check) => check.status === 'flagged')
      .map((check) => ({ loan, check })),
  )
  const mlAnomalyCount = analysis?.mlModel?.outlierApplications ?? 0
  const investigations = analysis?.investigations ?? []
  const assessmentRows = loans.map((loan) => {
    const score = analysis
      ? findAnalysisScore(
          analysis.scores,
          [loan.name, loan.legalName],
          loan.workflow?.folder,
        )
      : undefined
    const borrowerAliases = new Set([
      normalizeBorrowerName(loan.name),
      normalizeBorrowerName(loan.legalName),
      normalizeBorrowerName(score?.borrower ?? ''),
    ])
    const relatedSignals = signals.filter((signal) =>
      signal.borrowers.some((borrower) =>
        borrowerAliases.has(normalizeBorrowerName(borrower)),
      ),
    )
    const knownRules = loan.workflow?.checks
    const missingDocuments = loan.workflow?.requiredDocuments.filter(
      (document) => document.status !== 'received',
    ) ?? []
    return {
      loan,
      score,
      knownRules,
      missingDocuments,
      novelFindings: relatedSignals.filter(
        (signal) => signal.classification === 'new_cross_deal_lead'
          && signal.riskDimension !== 'behavioral_anomaly',
      ),
      relationshipObservations: relatedSignals.filter(
        (signal) => signal.classification === 'shared_relationship_observation',
      ),
      behavioralFindings: relatedSignals.filter(
        (signal) => signal.riskDimension === 'behavioral_anomaly',
      ),
      documentFindings: relatedSignals.filter(
        (signal) => signal.classification === 'document_review_issue',
      ),
    }
  })
  const matchedAssessmentCount = assessmentRows.filter((row) => row.score).length
  const completeInputCount = assessmentRows.filter((row) =>
    row.score
    && row.knownRules?.length === 5
    && !row.knownRules.some((check) => check.status === 'not_evaluable')
    && !row.missingDocuments.length,
  ).length
  const selectedSignalScores = (analysis?.scores ?? []).filter((score) =>
    selectedSignal?.borrowers.some(
      (borrower) =>
        normalizeBorrowerName(score.borrower) ===
        normalizeBorrowerName(borrower),
    ),
  )
  const analysisStatus = analysis
    ? analysis.mlModel
      ? 'ISOLATION FOREST · TRAINED'
      : 'CROSS-DEAL ANALYSIS · CONNECTED'
    : analysisLoading
      ? 'ANALYSIS LOADING'
      : 'ANALYSIS SERVICE OFFLINE'

  return (
    <>
      <PageHeading
        eyebrow="REVIEW & FOLLOW-UP"
        title="All loan risk assessments"
        subtitle="Review every application, including cases with no detected lead or incomplete source data."
      >
        <button className="button button-secondary" onClick={onRefresh} disabled={analysisRefreshing}>
          <Activity size={15} /> {analysisRefreshing ? 'Recalculating…' : 'Recalculate'}
        </button>
        <span className={`model-status ${analysisError ? 'model-status-offline' : ''}`}>
          <span /> {analysisStatus}
        </span>
      </PageHeading>
      {analysis?.mlModel ? (
        <div className="ml-model-summary">
          <strong>Unsupervised ML model</strong>
          <span>
            Isolation Forest trained on {analysis.mlModel.trainingRows} historical deals without
            existing bank-rule labels; {analysis.mlModel.excludedKnownRuleRows} labeled deals were
            excluded. {mlAnomalyCount} current application(s) exceeded the
            {' '}{analysis.mlModel.thresholdPercentile}th-percentile review threshold.
          </span>
          <small>{analysis.mlModel.limitations}</small>
        </div>
      ) : null}
      <div className="demo-banner fraud-banner">
        <AlertCircle size={15} />
        <span><strong>Human review required.</strong> Cross-deal patterns are leads—not findings. Do not block or decline a borrower based on this index.</span>
      </div>

      <section className="fraud-metrics">
        <div className="fraud-metric"><span>Open findings</span><strong>{signals.length}</strong><small>{new Set(signals.flatMap((signal) => signal.borrowers)).size} connected applications</small></div>
        <div className="fraud-metric"><span>Historical matches</span><strong>{historicalMatchIds.length}</strong><small>Prior deals linked by evidence</small></div>
        <div className="fraud-metric"><span>Known-rule reviews</span><strong>{new Set(loans.filter((loan) => loan.knownRuleFlags?.length).map((loan) => loan.id)).size}</strong><small>Separate from novel score</small></div>
        <div className="fraud-metric"><span>Current packets</span><strong>{analysis?.summary.currentPackets ?? '—'}</strong><small>Scanned by analysis service</small></div>
        <div className="fraud-metric"><span>ML outliers</span><strong>{mlAnomalyCount}</strong><small>Historical-profile leads</small></div>
      </section>

      <section className="panel risk-assessment-panel" aria-label="All loan risk assessments">
        <div className="risk-assessment-heading">
          <div>
            <h2>Company-by-company assessment</h2>
            <p>
              Every current application is listed. Fraud review is separate from known
              controls, document completeness, credit decisions, and workflow status.
            </p>
          </div>
          <span>
            {analysis
              ? `${matchedAssessmentCount}/${loans.length} matched · ${completeInputCount} complete inputs`
              : 'Analysis unavailable'}
          </span>
        </div>
        <div className="risk-assessment-table-wrap">
          <table className="risk-assessment-table">
            <thead>
              <tr>
                <th>COMPANY</th>
                <th>REVIEW PRIORITY INDEX</th>
                <th>KNOWN BANK RULES</th>
                <th>CROSS-DEAL / BEHAVIOR</th>
                <th>DOCUMENT REVIEW</th>
                <th>CREDIT REVIEW</th>
                <th>OPERATIONS</th>
              </tr>
            </thead>
            <tbody>
              {assessmentRows.map((row) => {
                const { loan, score, knownRules } = row
                const flaggedRules = knownRules?.filter((check) => check.status === 'flagged').length ?? 0
                const unassessedRules = knownRules?.filter(
                  (check) => check.status === 'not_evaluable',
                ).length ?? 0
                const clearRules = knownRules?.filter(
                  (check) => check.status === 'clear',
                ).length ?? 0
                const reviewedRules = knownRules?.filter(
                  (check) => check.status === 'reviewed',
                ).length ?? 0
                const incompleteInputs = !knownRules
                  || knownRules.length !== 5
                  || knownRules.some((check) => check.status === 'not_evaluable')
                  || row.missingDocuments.length > 0
                const scoreBand: RiskLevel = score
                  ? score.score === 0 && incompleteInputs ? 'Not assessed'
                    : score.score === 0 ? 'No scored lead'
                    : score.score >= 76 ? 'High'
                      : score.score >= 51 ? 'Elevated'
                        : score.score >= 26 ? 'Moderate' : 'Low'
                  : 'Not assessed'
                return (
                  <tr key={loan.id}>
                    <td>
                      <button
                        className="assessment-company-button"
                        onClick={() => onOpenLoan(loan.id)}
                      >
                        <strong>{loan.name}</strong>
                        <small>{loan.id}</small>
                      </button>
                    </td>
                    <td>
                      {score ? (
                        <div className="assessment-priority">
                          <strong>
                            {score.score === 0 && incompleteInputs
                              ? 'Not assessed'
                              : `${score.score}/100`}
                          </strong>
                          <span className={`risk-pill risk-${riskTone[scoreBand]}`}>
                            {incompleteInputs ? 'Incomplete inputs' : scoreBand}
                          </span>
                          <small>
                            {incompleteInputs
                              ? 'Missing documents or bank checks could not be evaluated'
                              : score.score === 0
                                ? 'No eligible novel-evidence family scored'
                                : `${score.scoreBreakdown?.length ?? 0} scored evidence families`}
                          </small>
                          {score.evidenceConfidence !== undefined && score.evidenceConfidence > 0 && (
                            <small>
                              Evidence traceability {score.evidenceConfidence}%
                            </small>
                          )}
                        </div>
                      ) : (
                        <span className="assessment-not-evaluated">
                          Not assessed
                          <small>No matched analysis result</small>
                        </span>
                      )}
                    </td>
                    <td>
                      {knownRules ? (
                        <details className="assessment-details">
                          <summary>
                            {flaggedRules} triggered · {clearRules} clear
                            {unassessedRules ? ` · ${unassessedRules} not evaluable` : ''}
                            {reviewedRules ? ` · ${reviewedRules} reviewed` : ''}
                          </summary>
                          <ul>
                            {knownRules.map((check) => (
                              <li key={check.code}>
                                <strong>{check.code} · {check.status.replaceAll('_', ' ')}</strong>
                                <small>{check.evidence}</small>
                              </li>
                            ))}
                          </ul>
                        </details>
                      ) : (
                        <span className="assessment-not-evaluated">Not assessed</span>
                      )}
                    </td>
                    <td>
                      <AssessmentFindings
                        findings={[
                          ...row.novelFindings,
                          ...row.relationshipObservations,
                          ...row.behavioralFindings,
                        ]}
                        assessed={Boolean(score) && !incompleteInputs}
                        emptyLabel={incompleteInputs
                          ? 'No scored lead observed; inputs are incomplete'
                          : 'No lead detected in processed data'}
                      />
                    </td>
                    <td>
                      {loan.workflow ? (
                        <AssessmentDocuments
                          missing={row.missingDocuments.map((document) => document.label)}
                          findings={row.documentFindings}
                        />
                      ) : row.documentFindings.length ? (
                        <AssessmentFindings
                          findings={row.documentFindings}
                          assessed
                          emptyLabel="No document findings returned"
                        />
                      ) : (
                        <span className="assessment-not-evaluated">Not assessed</span>
                      )}
                    </td>
                    <td>
                      <span className="assessment-not-evaluated">
                        Not assessed by fraud analysis
                        <small>No credit-capacity score or lending decision is generated here</small>
                      </span>
                    </td>
                    <td>
                      {loan.workflow ? (
                        <div className="assessment-operations">
                          <strong>{loan.workflow.stage} · {loan.workflow.status.replaceAll('_', ' ')}</strong>
                          {loan.workflow.blockers.length ? (
                            <small>{loan.workflow.blockers.join('; ')}</small>
                          ) : (
                            <small>No current workflow blockers reported</small>
                          )}
                        </div>
                      ) : (
                        <span className="assessment-not-evaluated">Not assessed</span>
                      )}
                    </td>
                  </tr>
                )
              })}
              {!assessmentRows.length && (
                <tr><td colSpan={7}>No loan applications are available to assess.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="risk-assessment-footnote">
          A 0/100 index means no eligible novel-evidence family scored; it does not mean
          zero fraud risk. “Not assessed” means required results are unavailable or could
          not be matched. Rule checks that cannot run are shown as not evaluable.
          Behavioral anomalies and shared-relationship observations are not fraud findings.
        </p>
      </section>

      {knownRuleReviews.length ? (
        <section className="known-rule-reviews" aria-label="Known bank-rule checks">
          <div className="known-rule-heading">
            <div>
              <h2>Known bank-rule checks</h2>
              <p>Existing control triggers are listed separately from novel cross-deal leads and scores.</p>
            </div>
            <span>{knownRuleReviews.length} flagged checks</span>
          </div>
          <div className="known-rule-list">
            {knownRuleReviews.map(({ loan, check }) => (
              <button
                className="known-rule-row"
                key={`${loan.id}-${check.code}`}
                onClick={() => onOpenLoan(loan.id)}
              >
                <span className="known-rule-code">{check.code}</span>
                <span className="known-rule-copy">
                  <strong>{loan.name}</strong>
                  <small>{check.evidence}</small>
                </span>
                <span className="known-rule-action">Review application <ChevronRight size={14} /></span>
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {investigations.length ? (
        <section className="investigation-groups" aria-label="Application review assessments">
          <div className="investigation-groups-heading">
            <div><h2>Application review assessments</h2><p>Corroborated findings are grouped; priority is not a fraud probability or lending decision.</p></div>
            <span>{investigations.length} assessments</span>
          </div>
          <div className="investigation-group-list">
            {investigations.map((investigation) => {
              const dimensions = investigation.dimensions
              const firstSignal = signals.find((signal) =>
                investigation.signalIds.includes(signal.id),
              )
              const reviewRequired = investigation.status === 'review_required'
              return (
              <button
                key={investigation.id}
                className="investigation-group"
                onClick={() => {
                  if (firstSignal) setSelectedSignalId(firstSignal.id)
                  else {
                    const loan = loans.find((candidate) =>
                      investigation.borrowers.includes(candidate.name),
                    )
                    if (loan) onOpenLoan(loan.id)
                  }
                }}
              >
                <span className={`signal-severity ${
                  reviewRequired ? investigation.severity.toLowerCase() : 'assessment-neutral'
                }`}>
                  {reviewRequired ? <ShieldAlert size={16} /> : <CheckCircle2 size={16} />}
                </span>
                <span className="investigation-group-copy">
                  <strong>{investigation.title}</strong>
                  <small>{investigation.borrowers.join(' · ') || 'No linked applications'}</small>
                  <small>{investigation.patterns.slice(0, 3).join(' · ')}</small>
                  {dimensions && (
                    <small className="investigation-dimension-summary">
                      Evidence {dimensions.evidenceStrength} · Corroboration {dimensions.independentCorroboration}
                      {' · '}Coordination {dimensions.crossDealCoordination} · History {dimensions.historicalDeviation}
                      {' · '}Traceability {dimensions.evidenceReliability}
                    </small>
                  )}
                  {investigation.alternativeExplanations?.length ? (
                    <small className="investigation-context-summary">
                      Legitimate explanations remain unresolved
                    </small>
                  ) : (
                    <small>
                      {investigation.status === 'no_signals_detected'
                        ? 'No novel signals detected; not a clearance'
                        : 'Informational findings only'}
                    </small>
                  )}
                </span>
                <span className="investigation-group-score">
                  {investigation.priorityScore}<small>review priority /100</small>
                </span>
                {reviewRequired ? <ChevronRight size={16} /> : null}
              </button>
              )
            })}
          </div>
        </section>
      ) : null}

      <div className="fraud-layout">
        <section className="panel signal-list-panel">
          <div className="panel-heading">
            <div><h2>Signals for review</h2><p>Ranked by urgency · Select to inspect</p></div>
            <span className="small-count">{signals.length} OPEN</span>
          </div>
          <div className="signal-list">
            {signals.map((signal) => {
              const target = loans.find((loan) => loan.id === signal.loanIds[0])
              return (
                <button
                  key={signal.id}
                  className={`signal-row ${selectedSignal?.id === signal.id ? 'signal-row-active' : ''}`}
                  onClick={() => setSelectedSignalId(signal.id)}
                >
                  <span className={`signal-severity ${
                    (signal.riskDimension === 'behavioral_anomaly'
                      ? signal.behaviorSeverity ?? 'Moderate'
                      : signal.severity).toLowerCase()
                  }`}><ShieldAlert size={16} /></span>
                  <span className="signal-summary">
                    <strong>{signal.title}</strong>
                    <span>{target?.name ?? signal.borrowers[0] ?? 'Unmatched application'}</span>
                    <span className="signal-tags"><i>
                      {signal.riskDimension === 'behavioral_anomaly'
                        ? `Behavioral anomaly · ${signal.behaviorSeverity ?? 'Moderate'}`
                        : signal.classification === 'new_cross_deal_lead'
                          ? 'Cross-deal lead'
                          : signal.classification === 'document_review_issue'
                            ? 'Document review'
                            : 'Observed relationship'}
                    </i><i>{signal.borrowers.length} linked</i></span>
                  </span>
                  <span className="signal-confidence">
                    {signal.mlAnomaly
                      ? `${signal.mlAnomaly.percentile.toFixed(1)}`
                      : signal.reviewIndex ?? 'Not assessed'}
                    <small>
                      {signal.mlAnomaly ? 'ML percentile' : 'review index · not probability'}
                    </small>
                  </span>
                </button>
              )
            })}
            {!signals.length && (
              <div className="signal-empty">
                {analysisLoading
                  ? 'Loading source-backed analysis…'
                  : analysisError
                    ? `Analysis unavailable: ${analysisError}`
                    : 'No cross-deal review leads were returned. This does not certify applications as fraud-free.'}
              </div>
            )}
          </div>
          <div className="known-checks">
            <div className="history-heading">
              <h3><ShieldCheck size={15} /> Existing control baseline</h3>
              <span>{analysis?.baseline.historicalDeals ?? historicalReference.priorDeals} deals · {analysis?.baseline.knownFraudLabelCount ?? historicalReference.knownTrickExamples} labels</span>
            </div>
            <div className="check-list">
              {historicalReference.fraudLabels.map(({ code, count }) => {
                const descriptions: Record<string, string> = {
                  STMT_TAMPER: 'Statement reconciliation',
                  REV_INFLATION: 'Revenue vs. deposits',
                  ID_MISMATCH: 'Guarantor identity',
                  DUP_EIN: 'Duplicate EIN history',
                  SHELL_ADDRESS: 'Mailbox / PMB address',
                }
                return (
                  <div className="check-row" key={code}>
                    <Check size={14} /><code>{code}</code><span>{descriptions[code]}</span><strong>{analysis?.baseline.knownFraudLabels[code] ?? count}</strong>
                  </div>
                )
              })}
            </div>
            <div className="history-baseline">
              <span>Steps that took longer than expected</span>
              <strong>{historicalReference.slaBreaches} / {historicalReference.stageEvents}</strong>
              <div className="history-baseline-track">
                <i style={{ width: `${(historicalReference.slaBreaches / historicalReference.stageEvents) * 100}%` }} />
              </div>
            </div>
            <p className="history-footnote">{analysis?.baseline.labelNote ?? 'Historical labels cover only established controls and are not new-pattern fraud proof.'}</p>
            <div className="behavior-cohort-context">
              <strong>Historical deposit behavior · descriptive only</strong>
              {Object.entries(
                analysis?.baseline.depositBehavior?.historicalCohort?.repaymentCohorts ?? {},
              ).map(([status, cohort]) => (
                <span key={status}>
                  {status.replaceAll('_', ' ')}: {(cohort.meanRoundDepositPct * 100).toFixed(1)}% mean · {cohort.deals} deals
                </span>
              ))}
              <small>Repayment cohorts are not fraud labels and do not contribute to the review-priority index.</small>
            </div>
          </div>
        </section>

        <section className="panel evidence-panel">
          {selectedSignal ? (
            <>
              <div className="evidence-topline">
                <span className={`risk-pill risk-${riskTone[
                  selectedSignal.riskDimension === 'behavioral_anomaly'
                    ? selectedSignal.behaviorSeverity ?? 'Moderate'
                    : selectedSignal.severity
                ]}`}>
                  {selectedSignal.riskDimension === 'behavioral_anomaly'
                    ? `Behavioral anomaly · ${selectedSignal.behaviorSeverity ?? 'Moderate'}`
                    : `${selectedSignal.severity} review lead`}
                </span>
                <span className="signal-id">{selectedSignal.id} <i /> {selectedSignal.family.replaceAll('_', ' ')}</span>
              </div>
              <h2>{selectedSignal.title}</h2>
              <p className="evidence-description">{selectedSignal.description}</p>
              <div className="confidence-block">
                <div>
                  <span>Overall investigation priority · uncalibrated index</span>
                  <strong>{selectedSignal.reviewIndex}/100</strong>
                </div>
                <div className="confidence-track">
                  <i style={{
                    width: `${selectedSignal.reviewIndex}%`,
                  }} />
                </div>
                <span className="confidence-note">
                  {selectedSignal.classification === 'document_review_issue'
                    ? 'Document issue only. It does not contribute to fraud scoring.'
                    : analysis?.scoreMethod ?? 'Index is not a probability, fraud finding, or credit decision.'}
                </span>
                {selectedSignal.mlAnomaly ? (
                  <div className="behavioral-anomaly-summary">
                    <strong>
                      ML behavioral anomaly · {selectedSignal.behaviorSeverity ?? 'Moderate'}
                    </strong>
                    <span>
                      Isolation Forest rank {selectedSignal.mlAnomaly.percentile.toFixed(1)}th
                      percentile; this is separate from the investigation-priority index and
                      is not a fraud probability.
                    </span>
                  </div>
                ) : null}
                {selectedSignal.riskDimension === 'behavioral_anomaly'
                  && selectedSignal.depositBehavior ? (
                    <div className="behavioral-anomaly-summary">
                      <strong>
                        Deposit behavior · {selectedSignal.behaviorSeverity ?? 'Moderate'}
                      </strong>
                      <span>
                        Mean monthly whole-dollar rate {(
                          selectedSignal.depositBehavior.dealLevelMean * 100
                        ).toFixed(1)}%; historical {selectedSignal.depositBehavior.comparisonScope === 'industry'
                          ? `NAICS ${selectedSignal.depositBehavior.industryCode} peers`
                          : 'all-industry fallback'} median {(
                            selectedSignal.depositBehavior.historicalMedian * 100
                          ).toFixed(1)}%, 95th percentile {(
                            selectedSignal.depositBehavior.historical95thPercentile * 100
                              ).toFixed(1)}% across {selectedSignal.depositBehavior.comparisonDealCount} historical deals.
                        Historical transaction descriptions are unavailable for channel-matched comparison.
                      </span>
                    </div>
                  ) : null}
              </div>
              <details className="score-breakdown">
                <summary>Why this review-priority index?</summary>
                {selectedSignalScores.length ? selectedSignalScores.map((score) => (
                  <div className="score-breakdown-borrower" key={score.borrower}>
                    <h3>{score.borrower} · {score.score}/100</h3>
                    {(score.scoreBreakdown ?? []).length ? (
                      <ol>
                        {score.scoreBreakdown?.map((contribution) => (
                          <li key={contribution.family}>
                              <strong>
                                {contribution.label} · +{contribution.points}/25 · {contribution.urgency}
                              </strong>
                              <span className="score-contribution-rationale">
                                {contribution.rationale}
                              </span>
                              {contribution.signals.map((support) => (
                              <div className="score-breakdown-signal" key={support.id}>
                                <span>{support.title}</span>
                                {support.evidence.map((item, index) => (
                                  <small key={`${support.id}-${index}`}>
                                    {item.text} <code>{item.source}</code>
                                  </small>
                                ))}
                              </div>
                            ))}
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p>No scoring evidence families contributed to this application's index.</p>
                    )}
                    {(score.additionalFamilies ?? []).length ? (
                      <p>
                        Additional evidence not scored after the four-family cap:{' '}
                        {(score.additionalFamilies ?? []).join(', ')}.
                      </p>
                    ) : null}
                  </div>
                )) : (
                  <p>Per-family scoring details are unavailable from this analysis response.</p>
                )}
                <small>
                  Each family is scored 1–25: signal severity starts at Moderate 3,
                  Elevated 8, or High 13 points;
                  each additional source document adds 2 (up to 8), each additional
                  corroborating evidence type adds 2 (up to 4), and 2 or 3+ connected
                  applicants add 1 or 3. Urgency bands: 1–5 low, 6–10 moderate,
                  11–15 elevated, 16–20 high, 21–25 urgent. The total is capped at 100;
                  behavioral anomalies and document issues add no points. This is not a
                  fraud probability.
                </small>
              </details>
              <div className="evidence-heading"><h3>Supporting evidence</h3><span>DATA SOURCES ATTACHED</span></div>
              <ul className="evidence-list">
                {selectedSignal.evidence.map((item, index) => (
                  <li key={`${item.source}-${index}`}>
                    <span className="evidence-bullet"><Check size={12} /></span>
                    <span>{item.text}<small className="evidence-source">{item.source}</small></span>
                  </li>
                ))}
              </ul>
              <div className="linked-cases">
                <div className="linked-cases-heading"><h3><Link2 size={15} /> Current applications</h3><button onClick={() => selectedSignal.loanIds[0] && onOpenNetwork(selectedSignal.loanIds[0])}>Open network <ArrowRight size={13} /></button></div>
                {selectedSignal.loanIds.map((id) => {
                  const loan = loans.find((item) => item.id === id)
                  if (!loan) return null
                  return (
                    <button className="linked-case-row" key={id} onClick={() => onOpenLoan(id)}>
                      <div className={`business-avatar tiny ${loan.accent}`}>{loan.initials}</div>
                      <span><strong>{loan.name}</strong><small>{loan.id} · {money.format(loan.amount)}</small></span>
                      <span className={`risk-pill risk-${riskTone[loan.risk]}`}>{loan.risk}</span>
                      <ChevronRight size={15} />
                    </button>
                  )
                })}
                {selectedSignal.borrowers.filter((borrower) => !findLoanForBorrower(borrower, loans)).map((borrower) => (
                  <div className="linked-case-row linked-case-historical" key={borrower}>
                    <div className="business-avatar tiny violet"><FileText size={14} /></div>
                    <span><strong>{borrower}</strong><small>Source packet · no matching demo application record</small></span>
                  </div>
                ))}
              </div>
              {selectedSignal.historicalDeals.length > 0 && (
                <div className="historical-links">
                  <h3>Historical relationships <span>{selectedSignal.historicalDeals.length}</span></h3>
                  {selectedSignal.historicalDeals.slice(0, 5).map((deal) => (
                    <div className="historical-link-row" key={deal.id}>
                      <span><strong>{deal.id}</strong> · {deal.borrower}<small>{deal.date} · {deal.status} · repayment {deal.repaymentStatus || 'n/a'}</small></span>
                      <code>{deal.source}</code>
                    </div>
                  ))}
                  {selectedSignal.historicalDeals.length > 5 && (
                    <p className="history-footnote">Showing 5 of {selectedSignal.historicalDeals.length} linked historical deals. The relationship graph contains the full set.</p>
                  )}
                </div>
              )}
              <div className="investigation-actions">
                <h3>Recommended investigation</h3>
                <ul>{selectedSignal.recommendedActions.map((action) => <li key={action}>{action}</li>)}</ul>
              </div>
              <div className="evidence-actions">
                {mainLoan && (
                  <>
                    <button className="button button-danger" onClick={() => onAction(mainLoan, 'Request fraud hold')}><ShieldAlert size={15} /> Request hold</button>
                    <button className="button button-secondary" onClick={() => onAction(mainLoan, 'Escalate')}><ArrowUpRight size={15} /> Escalate</button>
                    <button className="button button-tertiary" onClick={() => onAction(mainLoan, 'Mark reviewed')}><Check size={15} /> Mark reviewed</button>
                  </>
                )}
              </div>
              <p className="human-gate-note"><Users size={14} /> Fraud Operations decides whether to place or release a hold.</p>
            </>
          ) : (
            <div className="analysis-empty-state">
              <ShieldCheck size={24} />
              <h2>{analysisLoading ? 'Analysis is loading' : analysisError ? 'Analysis service unavailable' : 'No cross-deal leads returned'}</h2>
              <p>{analysisError || 'A lack of surfaced leads does not certify applications as fraud-free. Known controls remain a separate review process.'}</p>
              {analysisError && <button className="button button-secondary" onClick={onRefresh}>Retry analysis</button>}
            </div>
          )}
        </section>
      </div>
    </>
  )
}

function AssessmentFindings({
  findings,
  assessed,
  emptyLabel,
}: {
  findings: Signal[]
  assessed: boolean
  emptyLabel: string
}) {
  if (!findings.length) {
    return (
      <span className="assessment-not-evaluated">
        {assessed ? emptyLabel : 'Not assessed'}
      </span>
    )
  }
  return (
    <details className="assessment-details">
      <summary>{findings.length} item{findings.length === 1 ? '' : 's'} for review</summary>
      <ul>
        {findings.map((finding) => (
          <li key={finding.id}>
            <strong>
              {finding.riskDimension === 'behavioral_anomaly'
                ? `Behavioral anomaly · ${finding.behaviorSeverity ?? 'Review'}`
                : finding.classification === 'shared_relationship_observation'
                  ? 'Relationship observation'
                  : 'Cross-deal lead'}: {finding.title}
            </strong>
            <small>{finding.description}</small>
            {finding.evidence.slice(0, 2).map((evidence, index) => (
              <small key={`${finding.id}-evidence-${index}`}>
                {evidence.text} · {evidence.source}
              </small>
            ))}
          </li>
        ))}
      </ul>
    </details>
  )
}

function AssessmentDocuments({
  missing,
  findings,
}: {
  missing: string[]
  findings: Signal[]
}) {
  if (!missing.length && !findings.length) {
    return <span className="assessment-not-evaluated">No current issue surfaced</span>
  }
  return (
    <details className="assessment-details">
      <summary>
        {missing.length ? `${missing.length} missing` : 'Complete'}
        {findings.length ? ` · ${findings.length} inconsistency` : ''}
      </summary>
      <ul>
        {missing.map((document) => (
          <li key={document}><strong>Missing: {document}</strong></li>
        ))}
        {findings.map((finding) => (
          <li key={finding.id}>
            <strong>{finding.title}</strong>
            <small>{finding.description}</small>
            {finding.evidence.slice(0, 2).map((evidence, index) => (
              <small key={`${finding.id}-evidence-${index}`}>
                {evidence.text} · {evidence.source}
              </small>
            ))}
          </li>
        ))}
      </ul>
    </details>
  )
}

type GraphNode = {
  id: string
  sourceId: string
  label: string
  kind: 'loan' | 'entity' | 'document' | 'vendor'
  x: number
  y: number
  risk?: RiskLevel
  loanId?: string
  status?: string
  source?: string
  borrower?: string
  date?: string
  sourceKind: string
}

function graphLabelLines(label: string, maxCharacters: number, maxLines = 2) {
  const words = label.trim().split(/\s+/).filter(Boolean)
  const lines: string[] = []
  let currentLine = ''

  for (const word of words) {
    const parts = word.match(new RegExp(`.{1,${maxCharacters}}`, 'g')) ?? [word]
    for (const part of parts) {
      if (currentLine && currentLine.length + part.length + 1 > maxCharacters) {
        lines.push(currentLine)
        currentLine = ''
      }
      currentLine = currentLine ? `${currentLine} ${part}` : part
    }
  }

  if (currentLine) lines.push(currentLine)
  if (lines.length > maxLines) {
    lines.length = maxLines
    const lastLine = maxLines - 1
    lines[lastLine] = `${lines[lastLine].slice(0, maxCharacters - 1)}…`
  }

  return lines.length ? lines : [label]
}

function NetworkExplorer({
  loans,
  initialSelection,
  analysis,
  onOpenLoan,
}: {
  loans: Loan[]
  initialSelection: string
  analysis: FraudAnalysis | null
  onOpenLoan: (id: string) => void
}) {
  const [selectedSourceId, setSelectedSourceId] = useState('')
  const [showAllLinks, setShowAllLinks] = useState(false)
  const [zoom, setZoom] = useState(1)
  const rawNodes = analysis?.graph.nodes ?? []
  const rawEdges = analysis?.graph.edges ?? []
  const appNodes = rawNodes.filter((node) => node.kind === 'application')
  const selectedApp = appNodes.find((node) =>
    findLoanForBorrower(node.label, loans)?.id === initialSelection,
  ) ?? appNodes[0]
  const focusSourceId = selectedSourceId || selectedApp?.id || ''
  const adjacency = new Map<string, Set<string>>()
  rawEdges.forEach((edge) => {
    adjacency.set(edge.source, (adjacency.get(edge.source) ?? new Set()).add(edge.target))
    adjacency.set(edge.target, (adjacency.get(edge.target) ?? new Set()).add(edge.source))
  })
  const visibleSourceIds = new Set<string>()
  const distances = new Map<string, number>()
  const queue = focusSourceId ? [focusSourceId] : []
  if (focusSourceId) {
    visibleSourceIds.add(focusSourceId)
    distances.set(focusSourceId, 0)
  }
  while (queue.length) {
    const sourceId = queue.shift()
    if (!sourceId) continue
    const depth = distances.get(sourceId) ?? 0
    if (depth >= 3) continue
    for (const neighbor of adjacency.get(sourceId) ?? []) {
      if (visibleSourceIds.has(neighbor)) continue
      const historicalCount = [...visibleSourceIds]
        .filter((id) => rawNodes.find((node) => node.id === id)?.kind === 'historical_deal')
        .length
      const neighborNode = rawNodes.find((node) => node.id === neighbor)
      if (neighborNode?.kind === 'historical_deal' && historicalCount >= 5) continue
      visibleSourceIds.add(neighbor)
      distances.set(neighbor, depth + 1)
      queue.push(neighbor)
    }
  }
  const visibleRawNodes = rawNodes.filter((node) => visibleSourceIds.has(node.id))
  const applicationNodes = visibleRawNodes.filter((node) => node.kind === 'application')
  const historicalNodes = visibleRawNodes.filter((node) => node.kind === 'historical_deal')
  const entityNodes = visibleRawNodes.filter(
    (node) => node.kind !== 'application' && node.kind !== 'historical_deal',
  )
  const appPositions = new Map(
    applicationNodes.map((node, index) => [
      node.id,
      { x: 90 + (760 * (index + 1)) / (applicationNodes.length + 1), y: 100 },
    ]),
  )
  const historyPositions = new Map(
    historicalNodes.map((node, index) => [
      node.id,
      { x: 90 + (760 * (index + 1)) / (historicalNodes.length + 1), y: 485 },
    ]),
  )
  const entityPositions = new Map(
    entityNodes.map((node, index) => [
      node.id,
      {
        x: 140 + (220 * (index % 4)),
        y: 235 + 85 * Math.floor(index / 4),
      },
    ]),
  )
  const nodes: GraphNode[] = visibleRawNodes.map((node) => {
    const loan = node.kind === 'application'
      ? findLoanForBorrower(node.label, loans)
      : undefined
    const visualKind: GraphNode['kind'] =
      node.kind === 'application' || node.kind === 'historical_deal'
        ? 'loan'
        : node.kind === 'document_identifier' ||
            node.kind === 'document_pattern' ||
            node.kind === 'financial_document'
          ? 'document'
          : node.kind === 'supplier' || node.kind === 'payment_account'
            ? 'vendor'
            : 'entity'
    const position =
      appPositions.get(node.id) ??
      historyPositions.get(node.id) ??
      entityPositions.get(node.id) ?? { x: 460, y: 290 }
    return {
      id: loan?.id ?? node.id,
      sourceId: node.id,
      label: node.label,
      kind: visualKind,
      x: position.x,
      y: position.y,
      risk: loan?.risk,
      loanId: loan?.id,
      status: node.status,
      source: node.source,
      borrower: node.borrower,
      date: node.date,
      sourceKind: node.kind,
    }
  })
  const graphEdges = rawEdges.filter(
    (edge) => visibleSourceIds.has(edge.source) && visibleSourceIds.has(edge.target),
  )
  const displayEdges = graphEdges
    .filter((edge) => showAllLinks || edge.signalIds.length > 0)
    .flatMap((edge) => {
      const from = nodes.find((node) => node.sourceId === edge.source)
      const to = nodes.find((node) => node.sourceId === edge.target)
      return from && to
        ? [{ ...edge, from, to, type: edge.signalIds.length > 0 ? 'review' : 'neutral' }]
        : []
    })
  const activeNode =
    nodes.find((node) => node.sourceId === selectedSourceId) ??
    nodes.find((node) => node.sourceId === focusSourceId) ??
    nodes[0]
  const activeSourceId = activeNode?.sourceId ?? ''
  const related = graphEdges
    .filter((edge) => edge.source === activeSourceId || edge.target === activeSourceId)
    .flatMap((edge) => [edge.source, edge.target])
  const connectedLoans = applicationNodes
    .filter((node) => node.id !== activeSourceId)
    .map((node) => findLoanForBorrower(node.label, loans))
    .filter((loan): loan is Loan => Boolean(loan))
  const activeLoan = activeNode?.loanId
    ? loans.find((loan) => loan.id === activeNode.loanId)
    : undefined
  const activeEdges = graphEdges.filter(
    (edge) => edge.source === activeSourceId || edge.target === activeSourceId,
  )
  const relatedSignals = analysis?.signals.filter((signal) =>
    activeEdges.some((edge) => edge.signalIds.includes(signal.id)),
  ) ?? []
  if (!analysis || !activeNode) {
    return (
      <>
        <PageHeading eyebrow="REVIEW & FOLLOW-UP" title="Connections between applications" subtitle="Links supported by documents and past loan records appear here." />
        <div className="network-disclaimer"><AlertTriangle size={15} /><span>Analysis unavailable. Start the backend service to load evidence-linked relationships.</span></div>
      </>
    )
  }

  return (
    <>
      <PageHeading
        eyebrow="REVIEW & FOLLOW-UP"
        title="Connections between applications"
        subtitle="Select a business or shared record to see how applications may be connected."
      >
        <button className="button button-secondary" onClick={() => setShowAllLinks((value) => !value)}>
          <GitBranch size={16} />
          {showAllLinks ? 'Hide unflagged links' : 'Show all connections'}
        </button>
      </PageHeading>
      <div className="network-disclaimer"><AlertTriangle size={15} /><span><strong>Investigation lead, not a finding.</strong> Every edge includes source evidence. Name normalization is for candidate linking only; confirm legal identity independently.</span></div>
      <div className="network-layout">
        <section className="panel network-panel">
          <div className="network-toolbar">
            <div className="network-legend">
              <span><i className="graph-dot graph-loan" /> Application</span>
              <span><i className="graph-dot graph-entity" /> Shared record</span>
              <span><i className="graph-line" /> Signal-supported relationship</span>
              {showAllLinks && <span><i className="graph-line graph-line-muted" /> Other documented link</span>}
            </div>
            <span className="graph-count">{applicationNodes.length} current applications · {historicalNodes.length} historical deals · {entityNodes.length} records</span>
          </div>
          <div className="graph-canvas">
            <svg viewBox="0 0 940 570" role="img" aria-label="Interactive relationship graph">
              <defs>
                <filter id="node-shadow" x="-60%" y="-60%" width="220%" height="220%">
                  <feDropShadow dx="0" dy="4" stdDeviation="7" floodColor="#06244a" floodOpacity=".09" />
                </filter>
              </defs>
              <g transform={`scale(${zoom})`}>
              {displayEdges.map((edge) => (
                  <line
                    key={`${edge.source}-${edge.target}-${edge.relationship}`}
                    x1={edge.from.x}
                    y1={edge.from.y}
                    x2={edge.to.x}
                    y2={edge.to.y}
                    className={edge.type === 'review' ? 'graph-edge graph-edge-review' : 'graph-edge graph-edge-neutral'}
                  />
              ))}
              {nodes.map((node) => {
                const isSelected = activeNode.sourceId === node.sourceId
                const isRelated = related.includes(node.sourceId)
                return (
                  <g
                    key={node.id}
                    className={`graph-node graph-node-${node.kind} ${isSelected ? 'graph-node-selected' : ''} ${isRelated ? 'graph-node-related' : ''}`}
                    onClick={() => setSelectedSourceId(node.sourceId)}
                    role="button"
                    tabIndex={0}
                    aria-label={`Select ${node.label}`}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') setSelectedSourceId(node.sourceId)
                    }}
                  >
                    <title>{node.label}</title>
                    {node.kind === 'loan' ? (
                      <>
                        <circle cx={node.x} cy={node.y} r="39" className="graph-node-halo" />
                        <circle cx={node.x} cy={node.y} r="29" className={`graph-node-circle ${node.sourceKind === 'historical_deal' ? 'risk-node-low' : `risk-node-${riskTone[node.risk ?? 'Low']}`}`} filter="url(#node-shadow)" />
                        <text x={node.x} y={node.y + 4} className="graph-initials">{loans.find((loan) => loan.id === node.loanId)?.initials ?? node.label.slice(0, 3).toUpperCase()}</text>
                        {(node.risk === 'High' || node.risk === 'Elevated') && <circle cx={node.x + 21} cy={node.y - 21} r="6" className="graph-warning-dot" />}
                        <text x={node.x} y={node.y + 49} className="graph-label">
                          {graphLabelLines(node.label, 22).map((line, index) => (
                            <tspan key={index} x={node.x} dy={index === 0 ? 0 : 13}>{line}</tspan>
                          ))}
                        </text>
                      </>
                    ) : node.kind === 'document' ? (
                      <>
                        <rect x={node.x - 80} y={node.y - 24} width="160" height="48" rx="14" className="graph-entity-box graph-document-box" />
                        <Fingerprint x={node.x - 67} y={node.y - 9} width={18} height={18} className="graph-entity-icon" />
                        <text x={node.x + 17} y={node.y - 2} className="graph-entity-label">
                          {graphLabelLines(node.label, 18).map((line, index) => (
                            <tspan key={index} x={node.x + 17} dy={index === 0 ? 0 : 12}>{line}</tspan>
                          ))}
                        </text>
                      </>
                    ) : (
                      <>
                        <rect x={node.x - 76} y={node.y - 21} width="152" height="42" rx="14" className={`graph-entity-box ${node.kind === 'vendor' ? 'graph-vendor-box' : ''}`} />
                        {node.kind === 'vendor' ? <HandCoins x={node.x - 61} y={node.y - 9} width={17} height={17} className="graph-entity-icon" /> : <Users x={node.x - 60} y={node.y - 9} width={17} height={17} className="graph-entity-icon" />}
                        <text x={node.x + 20} y={node.y - 2} className="graph-entity-label">
                          {graphLabelLines(node.label, 17).map((line, index) => (
                            <tspan key={index} x={node.x + 20} dy={index === 0 ? 0 : 11}>{line}</tspan>
                          ))}
                        </text>
                      </>
                    )}
                  </g>
                )
              })}
              </g>
            </svg>
            <div className="graph-controls">
              <button aria-label="Zoom in" onClick={() => setZoom((value) => Math.min(1.25, value + 0.1))}>+</button>
              <button aria-label="Zoom out" onClick={() => setZoom((value) => Math.max(0.8, value - 0.1))}>−</button>
              <button aria-label="Reset graph" onClick={() => { setZoom(1); setSelectedSourceId(selectedApp?.id ?? '') }}>↻</button>
            </div>
            <div className="graph-caption"><span className="graph-caption-dot" /> Source-backed relationships · select a node for evidence</div>
          </div>
        </section>
        <aside className="panel node-detail-panel">
          <div className="node-detail-kicker"><span className="signal-live" /> SELECTED NODE</div>
          <div className="node-detail-title">
            <div className={`business-avatar ${activeLoan?.accent ?? 'violet'}`}>
              {activeLoan?.initials ?? <Link2 size={17} />}
            </div>
            <div><h2>{activeNode.label}</h2><span>{activeNode.sourceKind.replaceAll('_', ' ')}{activeNode.status ? ` · ${activeNode.status}` : ''}</span></div>
          </div>
          <div className="node-detail-divider" />
          <div className="node-detail-stat"><span>Applications in view</span><strong>{applicationNodes.length}</strong></div>
          <div className="node-detail-stat"><span>Historical deals in view</span><strong>{historicalNodes.length}</strong></div>
          <div className="node-detail-stat"><span>Documented relationships</span><strong>{activeEdges.length}</strong></div>
          <div className="node-review-note"><AlertTriangle size={15} /><span>Connections suggest a review path. Confirm source documents before linking cases or placing a hold.</span></div>
          <div className="connected-list">
            <h3>Connected cases</h3>
            {connectedLoans.slice(0, 5).map((loan) => {
              const appNode = applicationNodes.find(
                (node) => findLoanForBorrower(node.label, loans)?.id === loan.id,
              )
              return (
              <button key={loan.id} onClick={() => appNode && setSelectedSourceId(appNode.id)}>
                <div className={`business-avatar tiny ${loan.accent}`}>{loan.initials}</div>
                <span><strong>{loan.name}</strong><small>{loan.id}</small></span>
                <ChevronRight size={15} />
              </button>
            )})}
            {historicalNodes.map((node) => (
              <button key={node.id} onClick={() => setSelectedSourceId(node.id)}>
                <div className="business-avatar tiny violet"><FileText size={13} /></div>
                <span><strong>{node.id}</strong><small>{node.borrower} · {node.status}</small></span>
                <ChevronRight size={15} />
              </button>
            ))}
            {activeLoan && (
              <button className="node-open-case" onClick={() => onOpenLoan(activeLoan.id)}>
                Open application <ArrowRight size={14} />
              </button>
            )}
          </div>
          {relatedSignals.length > 0 && (
            <div className="graph-detail-evidence">
              <h3>Signal evidence</h3>
              {relatedSignals.map((signal) => (
                <div key={signal.id}>
                  <strong>{signal.title}</strong>
                  {signal.evidence.slice(0, 2).map((item) => (
                    <p key={`${item.source}-${item.kind}`}>{item.text}<small>{item.source}</small></p>
                  ))}
                </div>
              ))}
            </div>
          )}
          {activeNode.source && <p className="graph-source-ref">Source: {activeNode.source}</p>}
        </aside>
      </div>
    </>
  )
}

function WorkflowMonitor({
  loans,
  workflow,
  onOpenLoan,
  onNavigate,
  onAction,
}: {
  loans: Loan[]
  workflow: WorkflowSnapshot | null
  onOpenLoan: (id: string) => void
  onNavigate: (view: ViewId) => void
  onAction: (loan: Loan, action: string) => void
}) {
  const pending = workflow?.pendingDecisions
    .map((decision) => loans.find((loan) => loan.workflow?.id === decision.dealId))
    .filter((loan): loan is Loan => Boolean(loan)) ??
    loans.filter((loan) =>
      ['Credit', 'Committee', 'Committed', 'Closed'].includes(loan.stage),
    )
  const activeAlerts = workflow?.alerts.filter((alert) => !alert.acknowledged) ?? []
  const onTrack = workflow
    ? loans.filter((loan) => {
        const hours = loan.workflow?.hoursInStage ?? 0
        const sla = loan.workflow?.slaHours ?? 0
        return sla > 0 && hours / sla < 0.75
      }).length
    : 0
  const approaching = workflow
    ? loans.filter((loan) => {
        const hours = loan.workflow?.hoursInStage ?? 0
        const sla = loan.workflow?.slaHours ?? 0
        return sla > 0 && hours / sla >= 0.75 && hours / sla < 1
      }).length
    : 0
  const escalatedDeals = new Set(
    activeAlerts.filter((alert) => alert.level >= 2).map((alert) => alert.deal_id),
  ).size
  return (
    <>
      <PageHeading
        eyebrow="REVIEW & FOLLOW-UP"
        title="Loan progress"
        subtitle="See where each application is, who needs to act, and when a task may be late."
      >
        <button className="button button-secondary" onClick={() => onNavigate('applications')}>
          <ListFilter size={16} /> View applications
        </button>
        <div className="live-status"><span /> DEMO CLOCK ACTIVE</div>
      </PageHeading>
      <div className="demo-banner"><Clock3 size={15} /><span>This demo uses a pretend clock. Choose “Next hour” to see how reminders work. No real notifications are sent.</span></div>
      <section className="workflow-stats">
        <div><span>Within expected time</span><strong>{onTrack}</strong><i className="stat-green"><ArrowUpRight size={13} /> Less than 75% of time limit</i></div>
        <div><span>Nearly late</span><strong>{approaching}</strong><i className="stat-amber"><Clock3 size={13} /> Reminder when time is up</i></div>
        <div><span>Past the time limit</span><strong>{escalatedDeals}</strong><i className="stat-rose"><ArrowUpRight size={13} /> Needs follow-up</i></div>
        <div><span>Waiting for a person</span><strong>{pending.length}</strong><i className="stat-blue"><Users size={13} /> A reviewer must act</i></div>
      </section>
      {workflow?.historicalSla.available && (
        <section className="panel historical-sla-panel">
          <div className="panel-heading">
            <div>
              <h2>How long past steps took</h2>
              <p>Challenge records show how often a step took longer than expected. This is not evidence of fraud.</p>
            </div>
            <strong>{workflow.historicalSla.breaches} of {workflow.historicalSla.events} <span>steps took longer than expected</span></strong>
          </div>
          <div className="historical-sla-grid">
            {workflow.historicalSla.byStage.map((item) => (
              <div key={item.stage}>
                <span>{item.stage}</span>
                <strong>{item.breaches} of {item.events}</strong>
                <small>{(item.breachRate * 100).toFixed(1)}% took longer</small>
              </div>
            ))}
          </div>
          <small className="historical-sla-source">Source: {workflow.historicalSla.source}</small>
        </section>
      )}
      <section className="workflow-board" aria-label="Loan applications by stage">
        <p className="workflow-board-help">Each column is a step in the loan process. Select an application to see what to do next.</p>
        {stages.map((stage) => {
          const stageLoans = loans.filter((loan) => loan.stage === stage)
          return (
            <div className="workflow-column" key={stage}>
              <div className="workflow-column-heading">
                <span><i className={`stage-dot stage-dot-${stage.toLowerCase()}`} />{stage}</span>
                <span>{stageLoans.length}</span>
              </div>
              <div className="workflow-cards">
                {stageLoans.map((loan, index) => (
                  <button className="workflow-card" key={loan.id} onClick={() => onOpenLoan(loan.id)}>
                    <div className="workflow-card-top"><span>{loan.id}</span><MoreHorizontal size={16} /></div>
                    <strong>{loan.name}</strong>
                    <span className="workflow-card-amount">{money.format(loan.amount)} <i /> {loan.product}</span>
                    <div className="workflow-owner"><div className={`mini-avatar ${loan.accent}`}>{loan.initials.slice(0, 1)}</div><span>{loan.workflow?.ownerName ?? (stage === 'Credit' ? loan.creditOfficer : loan.relationshipManager)}</span></div>
                    {loan.workflow?.fraudHold !== 'none' ? (
                      <span className="workflow-alert high-alert"><ShieldAlert size={13} /> Fraud hold {loan.workflow?.fraudHold}</span>
                    ) : loan.missingDocuments.length ? (
                      <span className="workflow-alert doc-alert"><FileText size={13} /> {loan.missingDocuments[0]}</span>
                    ) : (
                      <span className="workflow-sla"><Clock3 size={12} /> {loan.workflow ? `${loan.workflow.hoursInStage}h of ${loan.workflow.slaHours ?? '—'}h limit` : index === 0 ? 'Within time limit' : '6h in stage'}</span>
                    )}
                  </button>
                ))}
                {stageLoans.length === 0 && <div className="workflow-empty">No applications</div>}
              </div>
            </div>
          )
        })}
      </section>
      {workflow && (
        <section className="panel pending-panel">
          <div className="panel-heading">
            <div><h2>Time-limit reminders</h2><p>Reminders created when a loan step has taken longer than expected.</p></div>
            <span className="results-count">{activeAlerts.length} unacknowledged</span>
          </div>
          {activeAlerts.length ? (
            <div className="pending-decision-list">
              {activeAlerts.map((alert) => {
                const loan = loans.find((item) => item.workflow?.id === alert.deal_id)
                return (
                  <div key={alert.id} className="pending-decision-row">
                    <div className="pending-avatar"><Clock3 size={16} /></div>
                    <button className="alert-message" onClick={() => loan && onOpenLoan(loan.id)}>
                      <strong>{alert.message}</strong>
                      <span>Level {alert.level} · {alert.stage} · Routed to {alert.owner}</span>
                    </button>
                    {loan && (
                      <button
                        className="button button-secondary"
                        onClick={() => onAction(loan, `acknowledge_alert:${alert.id}:${alert.ownerId}`)}
                      >
                        Acknowledge
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          ) : <div className="workflow-empty">No time-limit reminders right now.</div>}
        </section>
      )}
      <section className="panel pending-panel">
        <div className="panel-heading">
          <div><h2>Pending human decisions</h2><p>These steps cannot proceed without an authorized reviewer.</p></div>
          <button className="text-link" onClick={() => onNavigate('applications')}>Review queue <ArrowRight size={14} /></button>
        </div>
        <div className="pending-decision-list">
          {pending.slice(0, 8).map((loan) => (
            <button key={loan.id} className="pending-decision-row" onClick={() => onOpenLoan(loan.id)}>
              <div className="pending-avatar"><Users size={16} /></div>
              <div><strong>{loan.name}</strong><span>{loan.nextAction} · {loan.stage}</span></div>
              <span className="pending-owner">{loan.workflow?.ownerName ?? (loan.stage === 'Credit' ? loan.creditOfficer : loan.relationshipManager)}</span>
              <span className={`risk-pill risk-${riskTone[loan.risk]}`}>{loan.status}</span>
              <ChevronRight size={16} />
            </button>
          ))}
          {!pending.length && <div className="workflow-empty">No human decisions are currently pending.</div>}
        </div>
      </section>
    </>
  )
}

function LoanDrawer({
  loan,
  onClose,
  onAction,
  onDownload,
  canDownload,
}: {
  loan: Loan
  onClose: () => void
  onAction: (action: string) => void
  onDownload: (filename: string) => void
  canDownload: boolean
}) {
  const [tab, setTab] = useState<DetailTab>('Overview')
  const isEquipment = loan.product.toLowerCase().includes('equipment')
  const workflow = loan.workflow
  const documents = workflow
    ? workflow.requiredDocuments.map((document) => ({
        name: document.label,
        status: document.status === 'received' ? 'Received' : 'Missing',
        source: document.source,
      }))
    : [
        { name: 'Signed loan application', status: 'Received', source: null },
        { name: 'Guarantor photo ID', status: 'Received', source: null },
        { name: 'Certificate of formation', status: 'Received', source: null },
        { name: 'IRS EIN letter', status: 'Received', source: null },
        { name: 'Financial statements', status: 'Received', source: null },
        { name: '2025 tax return / extension', status: 'Received', source: null },
        {
          name: 'Bank statements · May–October',
          status: loan.missingDocuments.some((item) => item.toLowerCase().includes('june'))
            ? 'Needs review'
            : 'Received',
          source: null,
        },
        ...(isEquipment
          ? [{
              name: 'Equipment quote',
              status: loan.missingDocuments.includes('Equipment quote') ? 'Missing' : 'Received',
              source: null,
            }]
          : []),
        { name: 'Borrower request letter', status: 'Received', source: null },
      ]
  const actionStages = ['Credit', 'Committee', 'Committed']
  const humanGate = actionStages.includes(loan.stage)
  const canAdvance = Boolean(
    workflow && !workflow.blockers.length && !humanGate && loan.stage !== 'Closed',
  )

  return (
    <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <aside className="loan-drawer" aria-label={`${loan.name} application details`}>
        <div className="drawer-topbar">
          <span>APPLICATION <i /> {loan.id}</span>
          <button className="icon-button" aria-label="Close application details" onClick={onClose}><X size={19} /></button>
        </div>
        <div className="drawer-heading">
          <div className={`business-avatar large ${loan.accent}`}>{loan.initials}</div>
          <div><h2>{loan.name}</h2><span>{loan.legalName}</span></div>
        </div>
        <div className="drawer-status-row">
          <StageBadge stage={loan.stage} />
          <span className={`risk-pill risk-${riskTone[loan.risk]}`}>
            {loan.risk === 'No scored lead'
              ? 'No novel lead · 0/100'
              : loan.risk === 'Not assessed'
                ? 'Review index not assessed'
                : `${loan.risk} review priority · ${loan.riskScore}/100`}
          </span>
        </div>
        <div className="drawer-tabs" role="tablist">
          {(['Overview', 'Documents', 'Activity'] as DetailTab[]).map((item) => (
            <button key={item} role="tab" aria-selected={tab === item} className={tab === item ? 'drawer-tab-active' : ''} onClick={() => setTab(item)}>{item}</button>
          ))}
        </div>
        <div className="drawer-content">
          {tab === 'Overview' && (
            <>
              <div className="drawer-amount-card"><span>REQUESTED AMOUNT</span><strong>{workflow?.application.amountRaw?.value ?? money.format(loan.amount)}</strong><small>{loan.product} <i /> {workflow?.application.termRaw?.value ?? `${loan.term} month term`}</small></div>
              <section className="drawer-next-step" aria-label="What to do next">
                <span>WHAT TO DO NEXT</span>
                <strong>{loan.nextAction}</strong>
                <p>
                  {workflow?.blockers.length
                    ? `First, take care of: ${workflow.blockers.join('; ')}`
                    : humanGate
                      ? 'A reviewer needs to make a decision before this loan can move forward.'
                      : workflow
                        ? 'Use the action buttons below when you are ready. The system will explain if something is still needed.'
                        : 'Connect to the workflow service to see the next step for this application.'}
                </p>
              </section>
              <div className="drawer-section"><div className="drawer-section-heading"><h3>Application details</h3><button><MoreHorizontal size={17} /></button></div>
                <DetailRow label="Federal EIN" value={getApplicationValue(workflow?.application ?? {}, 'ein') || 'Not extracted'} source={workflow?.application.ein?.source} />
                <DetailRow label="Business address" value={getApplicationValue(workflow?.application ?? {}, 'businessAddress') || 'Not extracted'} source={workflow?.application.businessAddress?.source} />
                <DetailRow label="Guarantor" value={getApplicationValue(workflow?.application ?? {}, 'guarantor') || 'Not extracted'} source={workflow?.application.guarantor?.source} />
                <DetailRow label="Use of proceeds" value={loan.purpose} source={workflow?.application.purpose?.source} />
                <DetailRow label="Industry" value={loan.industry} source={workflow?.application.industry?.source} />
                <DetailRow label="Stated annual revenue" value={workflow?.application.revenueRaw?.value ?? money.format(loan.revenue)} source={workflow?.application.revenueRaw?.source} />
                <DetailRow label="Relationship manager" value={loan.relationshipManager} />
                <DetailRow label="Credit officer" value={loan.creditOfficer} />
                {loan.neededBy && <DetailRow label="Funds needed by" value={loan.neededBy} highlight source={workflow?.application.fundsNeededBy?.source} />}
              </div>
              {workflow && (
                <div className="drawer-section">
                  <div className="drawer-section-heading"><h3>Loan status</h3><span>{workflow.hoursInStage}h in this step · {workflow.slaHours ?? '—'}h time limit</span></div>
                  <DetailRow label="Assigned reviewer" value={`${workflow.ownerName} · ${workflow.team}`} />
                  <DetailRow label="Fraud hold" value={workflow.fraudHold.replaceAll('_', ' ')} />
                  <DetailRow label="Final fraud review" value={workflow.fraudReview} />
                  {workflow.blockers.map((blocker) => (
                    <div className="drawer-flag" key={blocker}><AlertCircle size={14} />{blocker}</div>
                  ))}
                </div>
              )}
              <div className="drawer-section"><div className="drawer-section-heading"><h3>Next action</h3><span className="next-action-pill"><Clock3 size={12} /> Pending</span></div>
                <div className="next-action-box"><AlertCircle size={16} /><span><strong>{loan.nextAction}</strong><small>{loan.flags[0] ?? 'Continue to the next required workflow step.'}</small></span></div>
              </div>
              {loan.flags.length > 0 && <div className="drawer-section"><div className="drawer-section-heading"><h3>Review notes</h3></div>{loan.flags.map((flag) => <div className="drawer-flag" key={flag}><AlertTriangle size={14} />{flag}</div>)}</div>}
              {workflow?.checks.length ? (
                <div className="drawer-section">
                  <div className="drawer-section-heading"><h3>Known-rule checks</h3><span>Separate from cross-deal leads</span></div>
                  {workflow.checks.map((check) => (
                    <div className="workflow-check" key={check.code}>
                      <strong>{check.code.replaceAll('_', ' ')}</strong>
                      <span className={`check-status check-${check.status}`}>{check.status.replaceAll('_', ' ')}</span>
                      <small>{check.evidence}</small>
                      {loan.stage === 'Credit' && ['flagged', 'not_evaluable'].includes(check.status) && (
                        <button className="text-link" onClick={() => onAction(`resolve_check:${check.code}`)}>
                          Record human review
                        </button>
                      )}
                    </div>
                  ))}
                  {workflow.creditMemo && <details className="credit-memo"><summary>System credit memo draft</summary><p>{workflow.creditMemo}</p></details>}
                </div>
              ) : null}
            </>
          )}
          {tab === 'Documents' && (
            <div className="drawer-section document-drawer-section">
              <div className="drawer-section-heading"><h3>Document checklist</h3><span>{loan.documents.received}/{loan.documents.required} received</span></div>
              <p className="drawer-section-subtitle">Requirements based on the Intake and Credit stage rules.</p>
              {documents.map(({ name, status, source }) => (
                <div className="drawer-document-row" key={name}>
                  <div className={`document-status-icon ${status === 'Received' ? 'received' : status === 'Missing' ? 'missing' : 'review'}`}>
                    {status === 'Received' ? <FileCheck2 size={15} /> : <FileText size={15} />}
                  </div>
                  <span>{name}{source && <small className="document-source">{source}</small>}</span>
                  <small className={status === 'Received' ? 'doc-status-good' : 'doc-status-warn'}>{status}</small>
                </div>
              ))}
              {workflow?.documents.map((document) => (
                <div className="drawer-document-row" key={document.path}>
                  <div className={`document-status-icon ${document.legible ? 'received' : 'review'}`}>
                    {document.legible ? <FileCheck2 size={15} /> : <FileText size={15} />}
                  </div>
                  <span>{document.name}<small className="document-source">{document.path}</small></span>
                  {canDownload ? (
                    <button className="icon-button" aria-label={`Download ${document.name}`} title="Download document" onClick={() => onDownload(document.name)}>
                      <Download size={15} />
                    </button>
                  ) : <small className={document.legible ? 'doc-status-good' : 'doc-status-warn'}>{document.legible ? 'Extracted' : 'Unreadable'}</small>}
                </div>
              ))}
            </div>
          )}
          {tab === 'Activity' && (
            <div className="drawer-section">
              <div className="drawer-section-heading"><h3>Application history</h3><span>SIM TIME</span></div>
              <div className="timeline">
                {workflow?.history.length ? workflow.history.map((event) => (
                  <TimelineEvent
                    key={`${event.event_type}-${event.created_at}`}
                    icon={<Activity size={13} />}
                    title={event.event_type.replaceAll('_', ' ')}
                    detail={`${event.detail} · ${event.actor}`}
                    time={new Intl.DateTimeFormat('en-US', {
                      dateStyle: 'medium',
                      timeStyle: 'short',
                      timeZone: 'America/Chicago',
                    }).format(new Date(event.created_at))}
                  />
                )) : (
                  <>
                    <TimelineEvent icon={<Check size={13} />} title="Documents extracted" detail={`${loan.documents.received} of ${loan.documents.required} required items received`} time="Current simulation" />
                    <TimelineEvent icon={<BriefcaseBusiness size={13} />} title={`Application in ${loan.stage}`} detail={loan.nextAction} time={workflow?.stageEnteredAt ?? 'Initial state'} />
                  </>
                )}
              </div>
            </div>
          )}
        </div>
        <div className="drawer-footer">
          {workflow && (
            <>
              {humanGate && (
                <>
                  <button className="button button-primary drawer-main-action" onClick={() => onAction('Approve')}><Check size={15} /> Approve &amp; advance</button>
                  <button className="button button-secondary" onClick={() => onAction('decline')}>Decline</button>
                </>
              )}
              {loan.stage === 'Product' && (
                <>
                  {!workflow.productConfigured && <button className="button button-primary drawer-main-action" onClick={() => onAction('configure_product')}>Confirm product &amp; pricing</button>}
                  {isEquipment && !workflow.vendorVerified && <button className="button button-secondary" onClick={() => onAction('verify_vendor')}>Record vendor verification</button>}
                </>
              )}
              {loan.stage === 'Closed' && (
                <>
                  {workflow.status !== 'closed' && workflow.fraudHold === 'requested' && <button className="button button-danger drawer-main-action" onClick={() => onAction('place_hold')}>Place fraud hold</button>}
                  {workflow.status !== 'closed' && workflow.fraudHold === 'placed' && <button className="button button-secondary" onClick={() => onAction('release_hold')}>Release fraud hold</button>}
                  {!workflow.disbursementVerified && <button className="button button-secondary" onClick={() => onAction('verify_disbursement')}>Verify disbursement account</button>}
                  <button className="button button-primary drawer-main-action" disabled={workflow.status === 'closed' || workflow.blockers.length > 0} onClick={() => onAction('release_funds')}>Release funds</button>
                </>
              )}
              {['Committed', 'Closed'].includes(loan.stage) && workflow.status !== 'closed' && workflow.fraudReview !== 'cleared' && workflow.fraudHold !== 'placed' && (
                <button className="button button-secondary" onClick={() => onAction('clear_fraud')}>Clear final fraud review</button>
              )}
              {!humanGate && loan.stage !== 'Closed' && (
                <button className="button button-primary drawer-main-action" disabled={!canAdvance} onClick={() => onAction('advance')}>Advance to next stage</button>
              )}
              {workflow.fraudHold === 'requested' && loan.stage !== 'Closed' && <button className="button button-danger" onClick={() => onAction('place_hold')}>Place fraud hold</button>}
              {workflow.fraudHold === 'placed' && loan.stage !== 'Closed' && <button className="button button-secondary" onClick={() => onAction('release_hold')}>Release fraud hold</button>}
              {workflow.status !== 'closed' && workflow.fraudHold === 'none' && <button className="button button-danger" onClick={() => onAction('Request fraud hold')}><ShieldAlert size={15} /> Request fraud hold</button>}
              <button className="button button-secondary drawer-secondary-action" onClick={() => onAction('Request information')}><Send size={15} /> Request info</button>
            </>
          )}
          {!workflow && <p className="human-gate-note">Connect to the workflow service to record auditable application actions.</p>}
        </div>
      </aside>
    </div>
  )
}

function DetailRow({ label, value, highlight = false, source }: { label: string; value: string; highlight?: boolean; source?: string }) {
  return <div className={`detail-row ${highlight ? 'detail-row-highlight' : ''}`}><span>{label}</span><strong>{value}{source && <small className="document-source">{source}</small>}</strong></div>
}

function TimelineEvent({ icon, title, detail, time }: { icon: ReactNode; title: string; detail: string; time: string }) {
  return <div className="timeline-event"><div className="timeline-icon">{icon}</div><div><strong>{title}</strong><span>{detail}</span><small>{time}</small></div></div>
}

export default App
