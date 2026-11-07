export type FraudEvidence = {
  text: string
  source: string
  kind: string
}

export type HistoricalDealReference = {
  id: string
  borrower: string
  date: string
  status: string
  repaymentStatus: string
  source: string
}

export type CrossDealSignal = {
  id: string
  family: string
  scoreFamily?: string
  title: string
  classification: 'new_cross_deal_lead' | 'shared_relationship_observation'
  severity: 'High' | 'Elevated' | 'Moderate'
  description: string
  borrowers: string[]
  historicalDeals: HistoricalDealReference[]
  evidence: FraudEvidence[]
  recommendedActions: string[]
  scoreContribution?: boolean
}

export type FraudGraphNode = {
  id: string
  label: string
  kind: string
  borrower?: string
  status?: string
  date?: string
  source?: string
}

export type FraudGraphEdge = {
  source: string
  target: string
  relationship: string
  evidence: string[]
  signalIds: string[]
}

export type FraudAnalysis = {
  generatedAt: string
  summary: {
    historicalDeals: number
    currentPackets: number
    crossDealSignals: number
    linkedApplications: number
    graphNodes: number
    graphRelationships: number
  }
  baseline: {
    historicalDeals: number
    knownFraudLabels: Record<string, number>
    knownFraudLabelCount: number
    labelNote: string
    behaviorNote: string
    depositBehavior: {
      historicalCohort: {
        available: boolean
        historicalDeals?: number
        referencePercentile?: number
        threshold?: number
        currentPacketsEvaluated?: number
        method?: string
        reason?: string
        repaymentCohorts?: Record<string, {
          deals: number
          meanRoundDepositPct: number
          medianRoundDepositPct: number
        }>
      }
    }
  }
  scoreMethod: string
  scores: Array<{
    borrower: string
    score: number
    families: string[]
    method: string
  }>
  signals: CrossDealSignal[]
  graph: {
    nodes: FraudGraphNode[]
    edges: FraudGraphEdge[]
  }
}

export type WorkflowApplicationValue = {
  value: string
  source: string
  numericValue?: number | null
}

export type WorkflowDocumentRequirement = {
  key: string
  label: string
  status: 'received' | 'missing'
  source: string | null
  monthCount: number | null
}

export type WorkflowDocument = {
  name: string
  category: string
  path: string
  pages: number
  legible: boolean
}

export type WorkflowCheck = {
  code: string
  status: 'flagged' | 'clear' | 'not_evaluable' | 'reviewed'
  evidence: string
}

export type WorkflowEvent = {
  event_type: string
  detail: string
  actor: string
  created_at: string
}

export type WorkflowLoan = {
  id: string
  folder: string
  borrower: string
  stage: string
  stageEnteredAt: string
  status: string
  owner: string
  ownerName: string
  team: string
  relationshipManager: string
  creditOfficer: string
  slaHours: number | null
  hoursInStage: number
  blockers: string[]
  fraudHold: 'none' | 'requested' | 'placed'
  fraudReview: 'pending' | 'cleared'
  requiredDocuments: WorkflowDocumentRequirement[]
  documents: WorkflowDocument[]
  checks: WorkflowCheck[]
  application: Record<string, WorkflowApplicationValue>
  creditMemo: string
  flags: string[]
  history: WorkflowEvent[]
  productConfigured: boolean
  vendorVerified: boolean
  disbursementVerified: boolean
  requiredActor: string
}

export type WorkflowAlert = {
  id: number
  deal_id: string
  dealId: string
  stage: string
  level: number
  message: string
  owner: string
  ownerId: string
  createdAt: string
  acknowledged: number
}

export type WorkflowSnapshot = {
  now: string
  pipeline: string[]
  loans: WorkflowLoan[]
  alerts: WorkflowAlert[]
  historicalSla: {
    available: boolean
    source: string
    events: number
    breaches: number
    breachRate: number | null
    byStage: Array<{
      stage: string
      events: number
      breaches: number
      breachRate: number
    }>
  }
  pendingDecisions: Array<{
    dealId: string
    borrower: string
    stage: string
    owner: string
    ownerName: string
    status: string
  }>
}

async function responseError(response: Response, fallback: string): Promise<Error> {
  const body = await response.json().catch(() => null) as {
    error?: string
    blockers?: string[]
  } | null
  const detail = body?.blockers?.length
    ? `${body.error ?? fallback}: ${body.blockers.join('; ')}`
    : body?.error ?? `${fallback} (${response.status}).`
  return new Error(detail)
}

export async function fetchFraudAnalysis(refresh = false): Promise<FraudAnalysis> {
  const response = refresh
    ? await fetch('/api/reload', { method: 'POST', cache: 'no-store' })
    : await fetch('/api/analysis', { cache: 'no-store' })
  if (!response.ok) throw await responseError(response, 'Analysis service returned an error')
  return response.json() as Promise<FraudAnalysis>
}

export async function fetchWorkflow(): Promise<WorkflowSnapshot> {
  const response = await fetch('/api/workflow', { cache: 'no-store' })
  if (!response.ok) throw await responseError(response, 'Workflow service returned an error')
  return response.json() as Promise<WorkflowSnapshot>
}

export async function postWorkflowAction(
  dealId: string,
  payload: Record<string, string>,
): Promise<WorkflowSnapshot> {
  const response = await fetch(`/api/workflow/${encodeURIComponent(dealId)}/actions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    cache: 'no-store',
  })
  if (!response.ok) throw await responseError(response, 'Workflow action failed')
  return response.json() as Promise<WorkflowSnapshot>
}

export async function advanceWorkflowClock(by: string): Promise<WorkflowSnapshot> {
  const response = await fetch('/api/workflow/advance-clock', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ by }),
    cache: 'no-store',
  })
  if (!response.ok) throw await responseError(response, 'Simulation clock update failed')
  return response.json() as Promise<WorkflowSnapshot>
}
