import { useEffect, useState } from 'react'
import { Building2, Mail, PhoneCall, Save, User } from 'lucide-react'
import { useGet, useRefresh } from '../api/hooks/useApi'
import { put } from '../api/mutate'
import { Badge, Button, Card, Spinner } from '../components/ui'
import { PageHeader, Field, Input, AsyncState } from '../components/kit'
import { useToast } from '../components/Toast'

type Profile = {
  email: string
  name: string
  phone: string
  company: string
  picture?: string
}

export default function Settings() {
  const { data, isLoading, error } = useGet<Profile>(['profile'], '/auth/profile')
  const refresh = useRefresh()
  const toast = useToast()
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [company, setCompany] = useState('')
  const [saving, setSaving] = useState(false)
  const [seeded, setSeeded] = useState(false)

  // Seed the form once from the server; afterwards the user's edits win.
  useEffect(() => {
    if (!data || seeded) return
    setName(data.name || '')
    setPhone(data.phone ? `+${data.phone}` : '')
    setCompany(data.company || '')
    setSeeded(true)
  }, [data, seeded])

  const save = async () => {
    setSaving(true)
    try {
      const res = await put<Profile & { status: string }>('/auth/profile', { name, phone, company })
      toast('Profile saved', 'success')
      setPhone(res.phone ? `+${res.phone}` : '')
      refresh(['profile'])
    } catch (e: any) {
      toast(`Could not save: ${e.message || e}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        sub="Your account details — used across the platform"
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,560px)_1fr]">
        <Card className="p-5">
          <AsyncState loading={isLoading} error={error}>
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                {data?.picture ? (
                  <img src={data.picture} alt="" referrerPolicy="no-referrer"
                    className="h-10 w-10 rounded-full object-cover ring-1 ring-black/5" />
                ) : (
                  <div className="grid h-10 w-10 place-items-center rounded-full bg-gradient-to-br from-indigo-400 to-indigo-600 text-[14px] font-bold text-white">
                    {(name || data?.email || 'A').trim().charAt(0).toUpperCase()}
                  </div>
                )}
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[14px] font-semibold">{name || 'Your account'}</span>
                  </div>
                  <div className="flex min-w-0 items-center gap-1.5 text-[12px] text-dim">
                    <Mail size={12} className="shrink-0" /> <span className="truncate">{data?.email}</span>
                  </div>
                </div>
              </div>

              <Field label="Full name">
                <div className="relative">
                  <User size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-dim" />
                  <Input value={name} onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. Angelo Fernandes" className="pl-9" />
                </div>
              </Field>

              <Field label="Your phone number">
                <div className="relative">
                  <PhoneCall size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-dim" />
                  <Input value={phone} onChange={(e) => setPhone(e.target.value)}
                    placeholder="+91 98765 43210" inputMode="tel" className="pl-9" />
                </div>
                <p className="mt-1 text-[11.5px] text-dim">
                  Include the country code. Test calls from your BYON numbers ring this phone,
                  so you can hear your agent exactly as callers do.
                </p>
              </Field>

              <Field label="Company (optional)">
                <div className="relative">
                  <Building2 size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-dim" />
                  <Input value={company} onChange={(e) => setCompany(e.target.value)}
                    placeholder="e.g. Dhronai Technologies" className="pl-9" />
                </div>
              </Field>

              <div className="flex items-center gap-3 pt-1">
                <Button onClick={save} disabled={saving}>
                  {saving ? <Spinner className="h-3.5 w-3.5" /> : <Save size={14} />}
                  {saving ? 'Saving…' : 'Save changes'}
                </Button>
              </div>
            </div>
          </AsyncState>
        </Card>

        <Card className="h-fit p-5">
          <div className="eyebrow mb-2">Why we ask</div>
          <ul className="space-y-2.5 text-[13px] text-soft">
            <li className="flex items-start gap-2">
              <PhoneCall size={14} className="mt-0.5 shrink-0 text-mint-600 dark:text-mint-300" />
              <span><span className="font-semibold">Phone</span> — the “Test call” button on your
              numbers dials <i>you</i>, from your own number, with your agent live on the line.</span>
            </li>
            <li className="flex items-start gap-2">
              <User size={14} className="mt-0.5 shrink-0 text-indigo-500 dark:text-indigo-300" />
              <span><span className="font-semibold">Name & company</span> — shown on your account
              and available to your agents as context.</span>
            </li>
          </ul>
        </Card>
      </div>
    </div>
  )
}
