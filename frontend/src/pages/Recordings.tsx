import { useEffect, useState } from 'react'
import { Download } from 'lucide-react'
import { useGet } from '../api/hooks/useApi'
import { Card, cx } from '../components/ui'
import { PageHeader, Table, THead, TH, TR, TD, Pagination, SearchInput, AsyncState } from '../components/kit'
import { AudioPlayer } from '../components/AudioPlayer'

type Recording = {
  folder: string
  call_id: string | null
  caller: string
  folder_dt: string
}

type RecordingsResponse = {
  recordings: Recording[]
  total: number
  page: number
  limit: number
  pages: number
}

function formatDate(dt: string | null | undefined): string {
  if (!dt) return '—'
  const d = new Date(dt)
  if (isNaN(d.getTime())) return dt
  return d.toLocaleString()
}

export default function Recordings() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')

  // Reset to first page whenever the search term changes.
  useEffect(() => {
    setPage(1)
  }, [search])

  const { data, isLoading, error } = useGet<RecordingsResponse>(
    ['recordings', page, search],
    `/calls/recordings?page=${page}&limit=50&search=${encodeURIComponent(search)}`,
  )

  const recordings = Array.isArray(data?.recordings) ? data!.recordings : []
  const total = data?.total ?? 0
  const pages = data?.pages ?? 1

  return (
    <div className="space-y-6">
      <PageHeader title="Recordings" sub="Call audio & metadata" />

      <Card className="p-5">
        <div className="mb-4">
          <SearchInput
            value={search}
            onChange={setSearch}
            placeholder="Search by contact / number"
            className="max-w-sm"
          />
        </div>

        <AsyncState
          loading={isLoading}
          error={error}
          empty={recordings.length === 0}
          emptyLabel="No recordings yet"
        >
          <Table>
            <THead>
              <TH>Contact</TH>
              <TH>Date</TH>
              <TH>Call ID</TH>
              <TH>Play</TH>
              <TH>Download</TH>
            </THead>
            <tbody>
              {recordings.map((r) => {
                const folder = r?.folder ?? ''
                const enc = encodeURIComponent(folder)
                const audioUrl = `/api/calls/recordings/${enc}/audio`
                const downloadUrl = `${audioUrl}?download=1`
                return (
                  <TR key={folder || r?.call_id || Math.random()}>
                    <TD className="font-medium text-[var(--text)]">{r?.caller || '—'}</TD>
                    <TD className="whitespace-nowrap text-dim">{formatDate(r?.folder_dt)}</TD>
                    <TD className="max-w-[160px] truncate font-mono text-[12px] text-dim" title={r?.call_id ?? ''}>
                      {r?.call_id || '—'}
                    </TD>
                    <TD>
                      <AudioPlayer src={audioUrl} />
                    </TD>
                    <TD>
                      <a
                        href={downloadUrl}
                        download
                        className={cx(
                          'inline-flex items-center justify-center gap-1.5 rounded-xl px-3 py-1.5 text-[12px] font-semibold transition-all active:scale-[.97]',
                          'text-soft bg-[var(--surface-2)] hover:bg-[var(--border)] ring-1 ring-[var(--border)]',
                        )}
                      >
                        <Download size={14} /> Download
                      </a>
                    </TD>
                  </TR>
                )
              })}
            </tbody>
          </Table>
        </AsyncState>

        <Pagination page={page} pages={pages} total={total} onPage={setPage} />
      </Card>
    </div>
  )
}
