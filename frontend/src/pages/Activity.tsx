import { useState } from 'react'
import { useGet } from '../api/hooks/useApi'
import { Card, Badge } from '../components/ui'
import {
  PageHeader, Table, THead, TH, TR, TD, Pagination,
  StatusBadge, SearchInput, Select, Segmented, AsyncState,
} from '../components/kit'
import { inr, dur } from '../lib/format'

type Call = {
  call_id?: string
  start_time?: string
  end_time?: string
  duration_seconds?: number
  est_stt_cost?: number
  est_tts_cost?: number
  est_total_cost?: number
  call_type?: 'inbound' | 'outbound'
  contact_name?: string
  contact_phone?: string
  status?: 'answered' | 'no_answer' | 'busy' | 'declined' | 'failed'
}

type HistoryResp = {
  calls?: Call[]
  total?: number
  page?: number
  limit?: number
  pages?: number
}

const fmtWhen = (s?: string): string => {
  if (!s) return '—'
  const d = new Date(s)
  return isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

export default function Activity() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [days, setDays] = useState(7)
  const [callType, setCallType] = useState('all')
  const [status, setStatus] = useState('all')

  const reset = (fn: () => void) => { fn(); setPage(1) }

  const path =
    `/calls/history?page=${page}&limit=50` +
    `&search=${encodeURIComponent(search)}` +
    `&days=${days}&call_type=${callType}&status=${status}`

  const { data, isLoading, error } = useGet<HistoryResp>(
    ['history', page, search, days, callType, status],
    path,
  )

  const calls = data?.calls ?? []

  return (
    <div className="space-y-6">
      <PageHeader title="Call Activity" sub="Paginated history of past calls" />

      <Card className="p-4">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <SearchInput
            value={search}
            onChange={(v) => reset(() => setSearch(v))}
            placeholder="Search call id or contact"
            className="min-w-[220px] flex-1"
          />
          <Segmented
            options={[
              { value: '1', label: 'Today' },
              { value: '7', label: '7 days' },
              { value: '30', label: '30 days' },
              { value: '90', label: '90 days' },
            ]}
            value={String(days)}
            onChange={(v) => reset(() => setDays(Number(v)))}
          />
          <Select value={callType} onChange={(e) => reset(() => setCallType(e.target.value))}>
            <option value="all">All types</option>
            <option value="inbound">Inbound</option>
            <option value="outbound">Outbound</option>
          </Select>
          <Select value={status} onChange={(e) => reset(() => setStatus(e.target.value))}>
            <option value="all">All status</option>
            <option value="answered">Answered</option>
            <option value="no_answer">No answer</option>
            <option value="busy">Busy</option>
            <option value="declined">Declined</option>
            <option value="failed">Failed</option>
          </Select>
        </div>

        <AsyncState loading={isLoading} error={error} empty={calls.length === 0} emptyLabel="No calls found">
          <Table>
            <THead>
              <TH>Date &amp; Time</TH>
              <TH>Contact</TH>
              <TH>Type</TH>
              <TH>Duration</TH>
              <TH>Status</TH>
              <TH>Call ID</TH>
              <TH className="text-right">Est. Cost</TH>
            </THead>
            <tbody>
              {calls.map((c, i) => (
                <TR key={c?.call_id ?? i}>
                  <TD className="whitespace-nowrap text-[var(--text)]">{fmtWhen(c?.start_time)}</TD>
                  <TD>
                    <div className="font-semibold text-[var(--text)]">{c?.contact_name ?? 'Unknown'}</div>
                    {c?.contact_phone && <div className="text-[12px] text-dim">{c.contact_phone}</div>}
                  </TD>
                  <TD>
                    {c?.call_type
                      ? <Badge tone={c.call_type === 'inbound' ? 'indigo' : 'mint'}>{c.call_type}</Badge>
                      : <span className="text-dim">—</span>}
                  </TD>
                  <TD className="tabular-nums">{dur(c?.duration_seconds)}</TD>
                  <TD><StatusBadge value={c?.status} /></TD>
                  <TD>
                    <span className="block max-w-[140px] truncate font-mono text-[11px] text-dim">
                      {c?.call_id ?? '—'}
                    </span>
                  </TD>
                  <TD className="text-right tabular-nums text-[var(--text)]">{inr(c?.est_total_cost, 2)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </AsyncState>

        <Pagination
          page={data?.page ?? page}
          pages={data?.pages ?? 1}
          total={data?.total}
          onPage={setPage}
        />
      </Card>
    </div>
  )
}
