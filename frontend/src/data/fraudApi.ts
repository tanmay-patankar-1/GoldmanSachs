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

export async function fetchFraudAnalysis(refresh = false): Promise<FraudAnalysis> {
  const response = refresh
    ? await fetch('/api/reload', { method: 'POST', cache: 'no-store' })
    : await fetch('/api/analysis', { cache: 'no-store' })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: string } | null
    throw new Error(body?.error ?? `Analysis service returned ${response.status}.`)
  }
  return response.json() as Promise<FraudAnalysis>
}
