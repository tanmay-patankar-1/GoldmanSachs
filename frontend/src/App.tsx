import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
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
  Search,
  Send,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
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
  fetchFraudAnalysis,
  type CrossDealSignal,
  type FraudAnalysis,
} from './data/fraudApi'
import './App.css'

type ViewId = (typeof navItems)[number]['id']
type DetailTab = 'Overview' | 'Documents' | 'Activity'
type Signal = CrossDealSignal & {
  loanIds: string[]
  reviewIndex: number
}

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

const riskTone: Record<RiskLevel, string> = {
  Low: 'low',
  Moderate: 'moderate',
  Elevated: 'elevated',
  High: 'high',
}

function App() {
  const [view, setView] = useState<ViewId>('overview')
  const [loans, setLoans] = useState(initialLoans)
  const [selectedLoanId, setSelectedLoanId] = useState<string | null>(null)
  const [selectedSignalId, setSelectedSignalId] = useState('')
  const [search, setSearch] = useState('')
  const [stageFilter, setStageFilter] = useState('All stages')
  const [riskFilter, setRiskFilter] = useState('All risk levels')
  const [toast, setToast] = useState('')
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [simHour, setSimHour] = useState(10)
  const [networkFocusId, setNetworkFocusId] = useState('DP-26019')
  const [fraudAnalysis, setFraudAnalysis] = useState<FraudAnalysis | null>(null)
  const [analysisError, setAnalysisError] = useState('')
  const [analysisLoading, setAnalysisLoading] = useState(true)
  const [analysisRefreshing, setAnalysisRefreshing] = useState(false)

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
  }, [])

  const signals: Signal[] = useMemo(() => {
    if (!fraudAnalysis) return []
    return fraudAnalysis.signals.map((signal) => {
      const loanIds = signal.borrowers
        .map((borrower) => findLoanForBorrower(borrower, loans)?.id)
        .filter((id): id is string => Boolean(id))
      const score = Math.max(
        0,
        ...signal.borrowers.map(
          (borrower) =>
            fraudAnalysis.scores.find(
              (item) =>
                normalizeBorrowerName(item.borrower) ===
                normalizeBorrowerName(borrower),
            )?.score ?? 0,
        ),
      )
      return { ...signal, loanIds, reviewIndex: score }
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

  const updateLoan = (id: string, updates: Partial<Loan>) => {
    setLoans((current) =>
      current.map((loan) => (loan.id === id ? { ...loan, ...updates } : loan)),
    )
  }

  const handleAction = (loan: Loan, action: string) => {
    if (action === 'Approve') {
      updateLoan(loan.id, {
        status: 'Approved · human review recorded',
        nextAction: 'Continue to next workflow step',
      })
    } else if (action === 'Request information') {
      updateLoan(loan.id, {
        status: 'Information requested',
        nextAction: 'Awaiting borrower response',
      })
    } else if (action === 'Request fraud hold') {
      updateLoan(loan.id, {
        status: 'Fraud hold requested',
        nextAction: 'Awaiting Fraud Operations decision',
      })
    } else if (action === 'Escalate') {
      updateLoan(loan.id, {
        status: 'Escalated to manager',
        nextAction: 'Manager review',
      })
    } else {
      updateLoan(loan.id, {
        status: 'Review completed',
        nextAction: 'Continue to next workflow step',
      })
    }
    notify(`${action} recorded for ${loan.name}. Demo only; no request was sent.`)
  }

  const navigate = (nextView: ViewId) => {
    setView(nextView)
    setMobileNavOpen(false)
    setSelectedLoanId(null)
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNavOpen ? 'sidebar-open' : ''}`}>
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <span>GS</span>
          </div>
          <div className="brand-name">
            <strong>DealPilot</strong>
            <span>COMMERCIAL LENDING</span>
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
          {(['WORKSPACE', 'INTELLIGENCE', 'OPERATIONS'] as const).map(
            (group) => (
              <div className="nav-group" key={group}>
                <p className="nav-group-label">{group}</p>
                {navItems
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
                        {'badge' in item && item.badge ? (
                          <span className="nav-badge">{item.badge}</span>
                        ) : null}
                      </button>
                    )
                  })}
              </div>
            ),
          )}
        </nav>

        <div className="sidebar-bottom">
          <div className="environment-status">
            <span className="online-dot" />
            <span>CHALLENGE SIMULATION</span>
          </div>
          <div className="profile-switch">
            <div className="avatar avatar-navy">AR</div>
            <div className="profile-copy">
              <strong>Alex Rivera</strong>
              <span>Lending operations</span>
            </div>
            <MoreHorizontal size={18} className="muted-icon" />
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
            <strong>{navItems.find((item) => item.id === view)?.label}</strong>
          </div>
          <div className="topbar-actions">
            <div className="sim-clock">
              <span className="clock-live-dot" />
              <span>SIM</span>
              <strong>Nov 4, 2026</strong>
              <span className="clock-divider" />
              <strong>
                {simHour % 12 || 12}:00 {simHour % 24 >= 12 ? 'PM' : 'AM'} CT
              </strong>
              <button
                className="clock-advance"
                aria-label="Advance demo clock by one hour"
                title="Advance demo clock by one hour"
                onClick={() => {
                  setSimHour((hour) => hour + 1)
                  notify('Demo clock advanced by one hour.')
                }}
              >
                +1h
              </button>
            </div>
            <button
              className="icon-button notification-button"
              aria-label="Notifications"
              onClick={() => navigate('workflow')}
            >
              <Bell size={18} />
              <span className="notification-dot" />
            </button>
            <div className="topbar-avatar">AR</div>
          </div>
        </header>

        <div className="page-content">
          {view === 'overview' && (
            <Dashboard
              loans={loans}
              onOpenLoan={(id) => setSelectedLoanId(id)}
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
              onOpenLoan={(id) => setSelectedLoanId(id)}
            />
          )}
          {view === 'fraud' && (
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
              onOpenLoan={(id) => setSelectedLoanId(id)}
              onOpenNetwork={(id) => {
                setNetworkFocusId(id)
                navigate('network')
              }}
              onAction={handleAction}
            />
          )}
          {view === 'network' && (
            <NetworkExplorer
              loans={loans}
              initialSelection={networkFocusId}
              analysis={fraudAnalysis}
              onOpenLoan={(id) => setSelectedLoanId(id)}
            />
          )}
          {view === 'workflow' && (
            <WorkflowMonitor
              loans={loans}
              onOpenLoan={(id) => setSelectedLoanId(id)}
              onNavigate={navigate}
            />
          )}
        </div>
      </main>

      {selectedLoan && (
        <LoanDrawer
          loan={selectedLoan}
          onClose={() => setSelectedLoanId(null)}
          onAction={(action) => handleAction(selectedLoan, action)}
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

function Dashboard({
  loans,
  onOpenLoan,
  onNavigate,
}: {
  loans: Loan[]
  onOpenLoan: (id: string) => void
  onNavigate: (view: ViewId) => void
}) {
  const active = loans.filter((loan) => loan.stage !== 'Closed').length
  const pending = loans.filter((loan) =>
    ['Credit', 'Committee', 'Committed'].includes(loan.stage),
  ).length
  const flagged = loans.filter(
    (loan) => loan.risk === 'High' || loan.risk === 'Elevated',
  ).length
  const attention = loans.filter(
    (loan) =>
      loan.missingDocuments.length > 0 ||
      loan.risk === 'High' ||
      loan.status.includes('Priority'),
  )

  return (
    <>
      <PageHeading
        eyebrow="WEDNESDAY, NOVEMBER 4, 2026"
        title="Portfolio overview"
        subtitle="Commercial lending · Sample week operations"
      >
        <button className="button button-secondary" onClick={() => onNavigate('applications')}>
          <BriefcaseBusiness size={16} />
          All applications
        </button>
        <button className="button button-primary" onClick={() => onNavigate('fraud')}>
          <Shield size={16} />
          Review alerts
          <span className="button-count">3</span>
        </button>
      </PageHeading>

      <div className="demo-banner">
        <span>
          <strong>Challenge simulation</strong>
          <span> Synthetic borrower data · Risk signals and actions are illustrative</span>
        </span>
        <span className="demo-banner-right">SAMPLE WEEK <span>·</span> 13 BORROWERS</span>
      </div>

      <section className="metric-grid" aria-label="Portfolio overview">
        <MetricCard
          label="Active applications"
          value={String(active)}
          note="Across all stages"
          delta="+3"
          tone="blue"
        />
        <MetricCard
          label="Pending approvals"
          value={String(pending)}
          note="Awaiting a decision"
          delta="2 need attention"
          tone="blue"
        />
        <MetricCard
          label="Fraud alerts"
          value={String(flagged)}
          note="Across 3 connected cases"
          delta="1 high priority"
          tone="rose"
        />
        <MetricCard
          label="Overdue / at risk"
          value="2"
          note="Approaching SLA limit"
          delta="View workflow"
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
            Open workflow monitor <ArrowRight size={14} />
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
            {(['Low', 'Moderate', 'Elevated', 'High'] as RiskLevel[]).map((risk) => (
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
                      <span className="risk-score">{loan.riskScore}/100</span>
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

  return (
    <>
      <PageHeading
        eyebrow="INTELLIGENCE"
        title="Fraud intelligence"
        subtitle="Evidence-linked cross-deal leads for analyst review. No signal is a fraud finding."
      >
        <button className="button button-secondary" onClick={onRefresh} disabled={analysisRefreshing}>
          <Activity size={15} /> {analysisRefreshing ? 'Recalculating…' : 'Recalculate'}
        </button>
        <span className={`model-status ${analysisError ? 'model-status-offline' : ''}`}>
          <span /> {analysis ? 'CROSS-DEAL ANALYSIS · CONNECTED' : analysisLoading ? 'ANALYSIS LOADING' : 'ANALYSIS SERVICE OFFLINE'}
        </span>
      </PageHeading>
      <div className="demo-banner fraud-banner">
        <AlertCircle size={15} />
        <span><strong>Human review required.</strong> Cross-deal patterns are leads—not findings. Do not block or decline a borrower based on this index.</span>
      </div>

      <section className="fraud-metrics">
        <div className="fraud-metric"><span>Open signals</span><strong>{signals.length}</strong><small>{new Set(signals.flatMap((signal) => signal.borrowers)).size} connected applications</small></div>
        <div className="fraud-metric"><span>Historical matches</span><strong>{historicalMatchIds.length}</strong><small>Prior deals linked by evidence</small></div>
        <div className="fraud-metric"><span>Established controls</span><strong>5</strong><small>From historical review rules</small></div>
        <div className="fraud-metric"><span>Current packets</span><strong>{analysis?.summary.currentPackets ?? '—'}</strong><small>Scanned by analysis service</small></div>
      </section>

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
                  <span className={`signal-severity ${signal.severity.toLowerCase()}`}><ShieldAlert size={16} /></span>
                  <span className="signal-summary">
                    <strong>{signal.title}</strong>
                    <span>{target?.name ?? signal.borrowers[0] ?? 'Unmatched application'}</span>
                    <span className="signal-tags"><i>{signal.classification === 'new_cross_deal_lead' ? 'New cross-deal' : 'Observed relationship'}</i><i>{signal.borrowers.length} linked</i></span>
                  </span>
                  <span className="signal-confidence">{signal.reviewIndex}<small>priority index</small></span>
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
              <span>Historical SLA breaches</span>
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
                <span className={`risk-pill risk-${riskTone[mainLoan?.risk ?? 'Moderate']}`}>{selectedSignal.severity} review lead</span>
                <span className="signal-id">{selectedSignal.id} <i /> {selectedSignal.family.replaceAll('_', ' ')}</span>
              </div>
              <h2>{selectedSignal.title}</h2>
              <p className="evidence-description">{selectedSignal.description}</p>
              <div className="confidence-block">
                <div><span>Review priority index</span><strong>{selectedSignal.reviewIndex}/100</strong></div>
                <div className="confidence-track"><i style={{ width: `${selectedSignal.reviewIndex}%` }} /></div>
                <span className="confidence-note">{analysis?.scoreMethod ?? 'Index is not a probability, fraud finding, or credit decision.'}</span>
              </div>
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
        <PageHeading eyebrow="INTELLIGENCE" title="Relationship network" subtitle="Source-backed links appear when the analysis service is running." />
        <div className="network-disclaimer"><AlertTriangle size={15} /><span>Analysis unavailable. Start the backend service to load evidence-linked relationships.</span></div>
      </>
    )
  }

  return (
    <>
      <PageHeading
        eyebrow="INTELLIGENCE"
        title="Relationship network"
        subtitle="Explore document- and history-backed links. Select a node to inspect its evidence."
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
                    {node.kind === 'loan' ? (
                      <>
                        <circle cx={node.x} cy={node.y} r="39" className="graph-node-halo" />
                        <circle cx={node.x} cy={node.y} r="29" className={`graph-node-circle ${node.sourceKind === 'historical_deal' ? 'risk-node-low' : `risk-node-${riskTone[node.risk ?? 'Low']}`}`} filter="url(#node-shadow)" />
                        <text x={node.x} y={node.y + 4} className="graph-initials">{loans.find((loan) => loan.id === node.loanId)?.initials ?? node.label.slice(0, 3).toUpperCase()}</text>
                        {(node.risk === 'High' || node.risk === 'Elevated') && <circle cx={node.x + 21} cy={node.y - 21} r="6" className="graph-warning-dot" />}
                        <text x={node.x} y={node.y + 54} className="graph-label">{node.label}</text>
                      </>
                    ) : node.kind === 'document' ? (
                      <>
                        <rect x={node.x - 71} y={node.y - 24} width="142" height="48" rx="14" className="graph-entity-box graph-document-box" />
                        <Fingerprint x={node.x - 58} y={node.y - 9} width={18} height={18} className="graph-entity-icon" />
                        <text x={node.x + 9} y={node.y + 4} className="graph-entity-label">{node.label.length > 20 ? `${node.label.slice(0, 19)}…` : node.label}</text>
                      </>
                    ) : (
                      <>
                        <rect x={node.x - 62} y={node.y - 21} width="124" height="42" rx="14" className={`graph-entity-box ${node.kind === 'vendor' ? 'graph-vendor-box' : ''}`} />
                        {node.kind === 'vendor' ? <HandCoins x={node.x - 48} y={node.y - 9} width={17} height={17} className="graph-entity-icon" /> : <Users x={node.x - 47} y={node.y - 9} width={17} height={17} className="graph-entity-icon" />}
                        <text x={node.x + 7} y={node.y + 4} className="graph-entity-label">{node.label.length > 20 ? `${node.label.slice(0, 19)}…` : node.label}</text>
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
  onOpenLoan,
  onNavigate,
}: {
  loans: Loan[]
  onOpenLoan: (id: string) => void
  onNavigate: (view: ViewId) => void
}) {
  const stalled = loans.filter((loan) => loan.nextAction.toLowerCase().includes('decision') || loan.risk === 'High')
  return (
    <>
      <PageHeading
        eyebrow="OPERATIONS"
        title="Workflow monitor"
        subtitle="Track stage ownership, pending human decisions, and service-level commitments."
      >
        <button className="button button-secondary" onClick={() => onNavigate('applications')}>
          <ListFilter size={16} /> View applications
        </button>
        <div className="live-status"><span /> SIM CLOCK ACTIVE</div>
      </PageHeading>
      <div className="demo-banner"><Clock3 size={15} /><span>Stage SLAs run on wall-clock simulation time. Advancing the clock is available in the header.</span></div>
      <section className="workflow-stats">
        <div><span>On track</span><strong>9</strong><i className="stat-green"><ArrowUpRight size={13} /> 69%</i></div>
        <div><span>Approaching SLA</span><strong>2</strong><i className="stat-amber"><Clock3 size={13} /> Alert at 100%</i></div>
        <div><span>Escalations due</span><strong>1</strong><i className="stat-rose"><ArrowUpRight size={13} /> At 150% SLA</i></div>
        <div><span>Human decisions</span><strong>4</strong><i className="stat-blue"><Users size={13} /> Awaiting response</i></div>
      </section>
      <section className="workflow-board" aria-label="Loan applications by stage">
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
                    <div className="workflow-owner"><div className={`mini-avatar ${loan.accent}`}>{loan.initials.slice(0, 1)}</div><span>{stage === 'Credit' ? loan.creditOfficer : loan.relationshipManager}</span></div>
                    {loan.risk === 'High' ? (
                      <span className="workflow-alert high-alert"><ShieldAlert size={13} /> Fraud review requested</span>
                    ) : loan.missingDocuments.length ? (
                      <span className="workflow-alert doc-alert"><FileText size={13} /> {loan.missingDocuments[0]}</span>
                    ) : (
                      <span className="workflow-sla"><Clock3 size={12} /> {index === 0 ? 'Within SLA' : '6h in stage'}</span>
                    )}
                  </button>
                ))}
                {stageLoans.length === 0 && <div className="workflow-empty">No applications</div>}
              </div>
            </div>
          )
        })}
      </section>
      <section className="panel pending-panel">
        <div className="panel-heading">
          <div><h2>Pending human decisions</h2><p>These steps cannot proceed without an authorized reviewer.</p></div>
          <button className="text-link" onClick={() => onNavigate('applications')}>Review queue <ArrowRight size={14} /></button>
        </div>
        <div className="pending-decision-list">
          {stalled.slice(0, 4).map((loan) => (
            <button key={loan.id} className="pending-decision-row" onClick={() => onOpenLoan(loan.id)}>
              <div className="pending-avatar"><Users size={16} /></div>
              <div><strong>{loan.name}</strong><span>{loan.nextAction} · {loan.stage}</span></div>
              <span className="pending-owner">{loan.stage === 'Credit' ? loan.creditOfficer : loan.relationshipManager}</span>
              <span className={`risk-pill risk-${riskTone[loan.risk]}`}>{loan.status}</span>
              <ChevronRight size={16} />
            </button>
          ))}
        </div>
      </section>
    </>
  )
}

function LoanDrawer({
  loan,
  onClose,
  onAction,
}: {
  loan: Loan
  onClose: () => void
  onAction: (action: string) => void
}) {
  const [tab, setTab] = useState<DetailTab>('Overview')
  const isEquipment = loan.product.toLowerCase().includes('equipment')
  const documents = [
    ['Signed loan application', 'Received'],
    ['Guarantor photo ID', 'Received'],
    ['Certificate of formation', 'Received'],
    ['IRS EIN letter', 'Received'],
    ['Financial statements', 'Received'],
    ['2025 tax return / extension', 'Received'],
    ['Bank statements · May–October', loan.missingDocuments.some((item) => item.toLowerCase().includes('june')) ? 'Needs review' : 'Received'],
    ...(isEquipment ? [['Equipment quote', loan.missingDocuments.includes('Equipment quote') ? 'Missing' : 'Received']] : []),
    ['Borrower request letter', 'Received'],
  ]

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
        <div className="drawer-status-row"><StageBadge stage={loan.stage} /><span className={`risk-pill risk-${riskTone[loan.risk]}`}>{loan.risk} risk · {loan.riskScore}/100</span></div>
        <div className="drawer-tabs" role="tablist">
          {(['Overview', 'Documents', 'Activity'] as DetailTab[]).map((item) => (
            <button key={item} role="tab" aria-selected={tab === item} className={tab === item ? 'drawer-tab-active' : ''} onClick={() => setTab(item)}>{item}</button>
          ))}
        </div>
        <div className="drawer-content">
          {tab === 'Overview' && (
            <>
              <div className="drawer-amount-card"><span>REQUESTED AMOUNT</span><strong>{money.format(loan.amount)}</strong><small>{loan.product} <i /> {loan.term} month term</small></div>
              <div className="drawer-section"><div className="drawer-section-heading"><h3>Application details</h3><button><MoreHorizontal size={17} /></button></div>
                <DetailRow label="Use of proceeds" value={loan.purpose} />
                <DetailRow label="Industry" value={loan.industry} />
                <DetailRow label="Stated annual revenue" value={money.format(loan.revenue)} />
                <DetailRow label="Relationship manager" value={loan.relationshipManager} />
                <DetailRow label="Credit officer" value={loan.creditOfficer} />
                {loan.neededBy && <DetailRow label="Funds needed by" value={loan.neededBy} highlight />}
              </div>
              <div className="drawer-section"><div className="drawer-section-heading"><h3>Next action</h3><span className="next-action-pill"><Clock3 size={12} /> Pending</span></div>
                <div className="next-action-box"><AlertCircle size={16} /><span><strong>{loan.nextAction}</strong><small>{loan.flags[0] ?? 'Continue to the next required workflow step.'}</small></span></div>
              </div>
              {loan.flags.length > 0 && <div className="drawer-section"><div className="drawer-section-heading"><h3>Review notes</h3></div>{loan.flags.map((flag) => <div className="drawer-flag" key={flag}><AlertTriangle size={14} />{flag}</div>)}</div>}
            </>
          )}
          {tab === 'Documents' && (
            <div className="drawer-section document-drawer-section">
              <div className="drawer-section-heading"><h3>Document checklist</h3><span>{loan.documents.received}/{loan.documents.required} received</span></div>
              <p className="drawer-section-subtitle">Requirements based on the Intake and Credit stage rules.</p>
              {documents.map(([name, status]) => (
                <div className="drawer-document-row" key={name}>
                  <div className={`document-status-icon ${status === 'Received' ? 'received' : status === 'Missing' ? 'missing' : 'review'}`}>
                    {status === 'Received' ? <FileCheck2 size={15} /> : <FileText size={15} />}
                  </div>
                  <span>{name}</span>
                  <small className={status === 'Received' ? 'doc-status-good' : 'doc-status-warn'}>{status}</small>
                </div>
              ))}
            </div>
          )}
          {tab === 'Activity' && (
            <div className="drawer-section">
              <div className="drawer-section-heading"><h3>Application history</h3><span>SIM TIME</span></div>
              <div className="timeline">
                <TimelineEvent icon={<Check size={13} />} title="Documents received" detail={`${loan.documents.received} of ${loan.documents.required} expected`} time="Today · 9:42 AM" />
                <TimelineEvent icon={<Sparkles size={13} />} title="AI review completed" detail={`Risk score ${loan.riskScore}/100 · ${loan.risk.toLowerCase()} risk`} time="Today · 9:36 AM" />
                <TimelineEvent icon={<FileText size={13} />} title="Application entered ${loan.stage}" detail={loan.nextAction} time="Today · 9:12 AM" />
                <TimelineEvent icon={<BriefcaseBusiness size={13} />} title="Application created" detail={`${loan.relationshipManager} assigned as relationship manager`} time="Nov 2 · 9:21 AM" />
              </div>
            </div>
          )}
        </div>
        <div className="drawer-footer">
          {loan.risk === 'High' ? (
            <button className="button button-danger drawer-main-action" onClick={() => onAction('Request fraud hold')}><ShieldAlert size={15} /> Request fraud hold</button>
          ) : (
            <button className="button button-primary drawer-main-action" onClick={() => onAction('Approve')}><Check size={15} /> Record review decision</button>
          )}
          <button className="button button-secondary drawer-secondary-action" onClick={() => onAction('Request information')}><Send size={15} /> Request info</button>
        </div>
      </aside>
    </div>
  )
}

function DetailRow({ label, value, highlight = false }: { label: string; value: string; highlight?: boolean }) {
  return <div className={`detail-row ${highlight ? 'detail-row-highlight' : ''}`}><span>{label}</span><strong>{value}</strong></div>
}

function TimelineEvent({ icon, title, detail, time }: { icon: ReactNode; title: string; detail: string; time: string }) {
  return <div className="timeline-event"><div className="timeline-icon">{icon}</div><div><strong>{title}</strong><span>{detail}</span><small>{time}</small></div></div>
}

export default App
