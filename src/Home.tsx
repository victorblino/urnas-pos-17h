import { useEffect, useMemo, useState } from 'react'
import './home.css'

type SectionReference = { uf: string, municipality: string, zone: string, section: string }

type Result = {
  uf?: string
  votersFromLogs: number
  beforeDecision: number
  afterDecision: number
  sectionsProcessed: number
  sectionsExpected?: number
  sectionsCoveragePercentage?: number
  sectionsWithVotesAfterDecision: number
  sectionsClosedBeforeDecision: number
  sectionsWithoutClosingRecord: number
  earliestClosingStartedAtBrasilia: string | null
  latestClosingStartedAtBrasilia: string | null
  earliestClosingSection?: SectionReference | null
  latestClosingSection?: SectionReference | null
}

type Summary = {
  status: 'partial' | 'complete'
  nationwide: Result | null
  states: Result[]
  source: { resultsUrl?: string }
}

type Section = {
  municipality: string
  zone: string
  section: string
  beforeDecision: number
  afterDecision: number
  timezoneOffset: number
  lastVoteAtLocal: string | null
  lastVoteAtBrasilia?: string | null
  closingStartedAtBrasilia: string | null
}

type StateDetails = {
  state: Result
  sections?: Section[]
  chunks?: string[]
}

const integer = new Intl.NumberFormat('pt-BR')
const percentage = new Intl.NumberFormat('pt-BR', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
})
const officialSource = 'https://resultados.tse.jus.br/oficial/app/index.html#/eleicao/6258/uf/br/cargo/1/vis/nominal/dados-de-urna/boletim-de-urna'
const decisionNews = 'https://www.poder360.com.br/poder-eleicoes-2026/tse-avisou-secoes-sobre-prorrogacao-as-17h49/'
const totalSecondRoundUrns = 499_248
const totalUrnsSource = 'https://www.tse.jus.br/comunicacao/noticias/2026/Outubro/presidente-do-tse-anuncia-reeleicao-de-lula-e-faz-balanco-do-2o-turno-das-eleicoes-2026'

function ratio(value: number, total: number) {
  return total ? value / total * 100 : 0
}

function toBrasilia(local: string | null, offset: number) {
  if (!local) return null
  const [hour, minute, second] = local.split(':').map(Number)
  const value = (hour * 3600 + minute * 60 + second - offset * 3600 + 86_400) % 86_400
  return [Math.floor(value / 3600), Math.floor(value % 3600 / 60), value % 60]
    .map((part) => String(part).padStart(2, '0'))
    .join(':')
}

function sectionUrl(section: SectionReference) {
  return `https://resultados.tse.jus.br/oficial/app/index.html#/eleicao/6258/uf/${section.uf.toLowerCase()}/mu/${section.municipality}/zn/${section.zone}/cargo/1/vis/nominal/se/${section.section}/dados-de-urna/boletim-de-urna`
}

function sectionLabel(section: SectionReference) {
  return `${section.uf} ${section.municipality}/${section.zone}/${section.section}`
}

export default function Home() {
  const [data, setData] = useState<Summary | null>(null)
  const [failed, setFailed] = useState(false)
  const [detailUf, setDetailUf] = useState('')
  const [details, setDetails] = useState<StateDetails | null>(null)
  const [detailFailed, setDetailFailed] = useState(false)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')

  useEffect(() => {
    fetch('/data/summary.json')
      .then((response) => {
        if (!response.ok) throw new Error()
        return response.json() as Promise<Summary>
      })
      .then(setData)
      .catch(() => setFailed(true))
  }, [])

  const states = useMemo(() => [...(data?.states ?? [])].sort((a, b) => (a.uf ?? '').localeCompare(b.uf ?? '')), [data])
  const activeUf = detailUf || states[0]?.uf || ''
  const result = data?.nationwide
  const source = data?.source.resultsUrl ?? officialSource
  const totalVotes = result?.votersFromLogs ?? 0
  const sessionsWithLateVotes = result?.sectionsWithVotesAfterDecision ?? 0
  const sessionPercentage = ratio(sessionsWithLateVotes, result?.sectionsProcessed ?? 0)
  const expectedSections = result?.sectionsExpected ?? totalSecondRoundUrns
  const coveragePercentage = result?.sectionsCoveragePercentage ?? ratio(result?.sectionsProcessed ?? 0, expectedSections)
  const filteredSections = useMemo(() => {
    const term = query.trim().toLowerCase()
    return (details?.sections ?? []).filter((section) => {
      if (filter === 'late' && section.afterDecision === 0) return false
      if (filter === 'closed-before' && (!section.closingStartedAtBrasilia || section.closingStartedAtBrasilia >= '17:03:00')) return false
      return !term || `${section.municipality} ${section.zone} ${section.section}`.includes(term)
    })
  }, [details, filter, query])

  useEffect(() => {
    if (!activeUf) return
    let active = true
    fetch(`/data/states/${activeUf.toLowerCase()}.json`)
      .then((response) => {
        if (!response.ok) throw new Error()
        return response.json() as Promise<StateDetails>
      })
      .then(async (value) => {
        if (!value.chunks) return value
        const chunks = await Promise.all(value.chunks.map(async (chunk) => {
          const response = await fetch(`/data/states/${chunk}`)
          if (!response.ok) throw new Error()
          return response.json() as Promise<{ sections: Section[] }>
        }))
        return { ...value, sections: chunks.flatMap((chunk) => chunk.sections) }
      })
      .then((value) => active && setDetails(value))
      .catch(() => active && setDetailFailed(true))
    return () => { active = false }
  }, [activeUf])

  return <main className="page">
    <section className="summary" aria-labelledby="title">
      <div className="heading">
        <div>
          <p className="eyebrow">ELEIÇÃO 2026 · SEGUNDO TURNO</p>
          <h1 id="title">Votos antes e após a decisão</h1>
          <p className="context">Às 17h03 foi divulgada a decisão que prorrogou a votação até as 19h. Este painel separa os registros de eleitor computado nos logs antes e a partir desse momento. Eles não distinguem votos válidos de branco ou nulo. <a href={decisionNews} target="_blank" rel="noreferrer">Entenda a cronologia</a>.</p>
        </div>
        {result && <p className="collection-status"><span>Dados em apuração</span><strong>{integer.format(result.sectionsProcessed)} de {integer.format(expectedSections)} seções</strong></p>}
      </div>

      {failed ? <p className="message" role="alert">Não foi possível carregar os dados.</p> : !result ? <p className="message">Aguardando dados processados.</p> : <>
        <div className="metrics">
          <article className="metric">
            <p>Registros pré decisão</p>
            <strong>{percentage.format(ratio(result.beforeDecision, totalVotes))}%</strong>
            <span>{integer.format(result.beforeDecision)} registros</span>
            <a href={source} target="_blank" rel="noreferrer">Fonte: TSE · dados de urna</a>
          </article>

          <article className="metric metric-after">
            <p>Registros pós decisão · 17h03</p>
            <strong>{percentage.format(ratio(result.afterDecision, totalVotes))}%</strong>
            <span>{integer.format(result.afterDecision)} registros</span>
            <a href={source} target="_blank" rel="noreferrer">Fonte: TSE · dados de urna</a>
          </article>
        </div>

        <div className="closing-data">
          <dl>
            <div><dt>Primeiro encerramento</dt><dd>{result.earliestClosingStartedAtBrasilia ? `${result.earliestClosingStartedAtBrasilia} BRT` : '—'}{result.earliestClosingSection && <a href={sectionUrl(result.earliestClosingSection)} target="_blank" rel="noreferrer">{sectionLabel(result.earliestClosingSection)}</a>}</dd></div>
            <div><dt>Último encerramento</dt><dd>{result.latestClosingStartedAtBrasilia ? `${result.latestClosingStartedAtBrasilia} BRT` : '—'}{result.latestClosingSection && <a href={sectionUrl(result.latestClosingSection)} target="_blank" rel="noreferrer">{sectionLabel(result.latestClosingSection)}</a>}</dd></div>
            <div><dt>Encerradas antes das 17h03</dt><dd>{integer.format(result.sectionsClosedBeforeDecision)}</dd></div>
            <div><dt>Sem horário de encerramento</dt><dd>{integer.format(result.sectionsWithoutClosingRecord)}</dd></div>
          </dl>
          <a href={source} target="_blank" rel="noreferrer">Fonte: logs oficiais das urnas · TSE</a>
        </div>

        <section className="details" aria-labelledby="details-title">
          <div className="details-heading">
            <div><h2 id="details-title">Consultar por seção</h2><p>{integer.format(filteredSections.length)} resultados em {activeUf || '—'}</p></div>
            <div className="controls">
              <label><span>UF</span><select value={activeUf} onChange={(event) => { setDetailUf(event.target.value); setDetails(null); setDetailFailed(false); setQuery('') }} disabled={!states.length}>{states.map((state) => <option key={state.uf} value={state.uf}>{state.uf}</option>)}</select></label>
              <label><span>Filtro</span><select value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">Todas</option><option value="late">Com voto pós decisão</option><option value="closed-before">Encerradas antes das 17h03</option></select></label>
              <label className="search"><span>Pesquisar</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Município, zona ou seção" /></label>
            </div>
          </div>

          <div className="table-wrap">
            {detailFailed ? <p className="table-message">Não foi possível carregar as seções desta UF.</p> : !details ? <p className="table-message">Carregando seções…</p> : !filteredSections.length ? <p className="table-message">Nenhuma seção encontrada.</p> : <table>
              <thead><tr><th>Município</th><th>Zona</th><th>Seção</th><th>Pré decisão</th><th>Pós decisão</th><th>Último voto</th><th>Encerramento BRT</th><th>Fonte</th></tr></thead>
              {/* shortcut: render 200 rows to keep the static page responsive, add virtualization if full browsing becomes necessary */}
              <tbody>{filteredSections.slice(0, 200).map((section) => {
                const sectionSource = sectionUrl({ uf: activeUf, ...section })
                const lastVoteBrasilia = section.lastVoteAtBrasilia ?? toBrasilia(section.lastVoteAtLocal, section.timezoneOffset)
                return <tr key={`${section.municipality}-${section.zone}-${section.section}`}><td>{section.municipality}</td><td>{section.zone}</td><td>{section.section}</td><td>{integer.format(section.beforeDecision)}</td><td className={section.afterDecision ? 'late-votes' : ''}>{integer.format(section.afterDecision)}</td><td className="time"><strong>{lastVoteBrasilia ? `${lastVoteBrasilia} BRT` : '—'}</strong>{section.lastVoteAtLocal && section.lastVoteAtLocal !== lastVoteBrasilia && <small>{section.lastVoteAtLocal} local</small>}</td><td>{section.closingStartedAtBrasilia ? `${section.closingStartedAtBrasilia} BRT` : '—'}</td><td><a href={sectionSource} target="_blank" rel="noreferrer">TSE</a></td></tr>
              })}</tbody>
            </table>}
          </div>
          {filteredSections.length > 200 && <p className="result-limit">Mostrando 200 resultados. Refine a pesquisa para localizar uma seção específica.</p>}
        </section>

        <footer className="summary-footer">
          <p><strong>{integer.format(result.sectionsProcessed)}</strong> de {integer.format(expectedSections)} logs analisados · {percentage.format(coveragePercentage)}% de cobertura</p>
          <p><strong>{integer.format(sessionsWithLateVotes)}</strong> seções com registro pós decisão · {percentage.format(sessionPercentage)}% das seções analisadas</p>
          <a href={totalUrnsSource} target="_blank" rel="noreferrer">Fonte do total: TSE</a>
          <a href="https://github.com/victorblino/urnas-pos-17h" target="_blank" rel="noreferrer">Metodologia e código</a>
        </footer>
      </>}
    </section>
  </main>
}
