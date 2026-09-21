import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "motion/react";
import { ArrowRight, Download, Gauge, Coins, HardDrives } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { useJobs } from "@/jobs/JobsContext";
import { Button, Card, FormatMorph, ProgressBar, StatusBadge, StatCardSkeleton, Skeleton } from "@/components/ui";
import { ErrorButton } from "@/components/ErrorButton";
import { StorageBreakdownBar } from "@/components/StorageBreakdownBar";
import { Stagger, Item } from "@/lib/motion";
import { formatBytes, formatDateTime, formatDateOrNull } from "@/lib/format";
import { DASHBOARD_ROW_GRID } from "@/lib/tableColumns";
import type { DashboardResponse } from "@/api/types";

export function DashboardPage() {
  const { user } = useAuth();
  const { jobs, refresh } = useJobs();
  const { error } = useToast();
  const [stats, setStats] = useState<DashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);

  // Pull the server history so the "recent" list isn't empty on a fresh login.
  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api
      .dashboard()
      .then((d) => active && setStats(d))
      .catch((e: Error) => error(e.message))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [error]);

  // Keep the "Credits remaining" stat in sync when a conversion completes and
  // JobsContext broadcasts the updated balance. Depends only on stable `error`.
  useEffect(() => {
    const onCreditsUpdated = () => {
      api
        .dashboard()
        .then((d) => setStats(d))
        .catch((e: Error) => error(e.message));
    };
    window.addEventListener("credits:updated", onCreditsUpdated);
    return () => window.removeEventListener("credits:updated", onCreditsUpdated);
  }, [error]);

  async function handleDownload(jobId: string) {
    try {
      await api.downloadConvertedFile(jobId);
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not download file");
    }
  }

  const recent = useMemo(() => jobs.slice(0, 5), [jobs]);

  const total = stats?.conversion_stats.total_jobs ?? jobs.length;
  const completed = stats?.conversion_stats.successful_jobs ?? jobs.filter((j) => j.status === "COMPLETED").length;
  const successRate = total ? Math.round((completed / total) * 100) : 0;
  const storage = stats?.storage_stats;
  const storagePct = storage?.used_percent ?? 0;
  // The API's `breakdown` is additive and may be absent on older backends:
  // fall back to the plain single-colour bar whenever there is no data.
  const hasStorageBreakdown = (storage?.breakdown?.length ?? 0) > 0;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <motion.div
        className="flex items-center justify-between"
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
      >
        <div>
          <h1 className="font-display text-2xl font-semibold">Dashboard</h1>
          <p className="text-sm text-muted">Welcome back, {user?.username ?? "there"}.</p>
        </div>
        <Link to="/app/convert">
          <Button>New conversion</Button>
        </Link>
      </motion.div>

      {/* Stat cards — "Credits remaining" is the featured card on the left,
          with the remaining three grouped to its right. */}
      {loading ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <StatCardSkeleton className="lg:col-span-1" />
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:col-span-2">
            <StatCardSkeleton />
            <StatCardSkeleton />
            <StatCardSkeleton className="sm:col-span-2" />
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Stagger className="lg:col-span-1">
            <Item className="h-full">
              <CreditStatCard
                value={String(stats?.credit_balance ?? 0)}
                resetAt={stats?.credits_reset_at}
              />
            </Item>
          </Stagger>

          <Stagger className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:col-span-2">
            <Item className="h-full">
              <StatCard icon={Gauge} label="Total conversions" value={String(total)} />
            </Item>
            <Item className="h-full">
              <StatCard icon={Download} label="Success rate" value={`${successRate}%`} />
            </Item>
            <Item className="h-full sm:col-span-2">
              <StatCard
                icon={HardDrives}
                label="Storage used"
                value={formatBytes(storage?.used_bytes ?? 0)}
                footer={
                  hasStorageBreakdown && storage ? (
                    <StorageBreakdownBar
                      usedBytes={storage.used_bytes}
                      limitBytes={storage.limit_bytes}
                      usedPercent={storage.used_percent}
                      breakdown={storage.breakdown}
                    />
                  ) : (
                    <ProgressBar value={storagePct} from="var(--color-primary)" />
                  )
                }
              />
            </Item>
          </Stagger>
        </div>
      )}

      {/* Recent conversions */}
      <Card className="overflow-hidden">
        <div className="flex items-center justify-between border-b border-outline px-5 py-4">
          <h2 className="font-display text-lg font-semibold">Recent conversions</h2>
          <Link to="/app/queue" className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">
            View all <ArrowRight size={16} />
          </Link>
        </div>

        {loading ? (
          <div className="space-y-3 px-5 py-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4">
                <Skeleton className="h-4 flex-1" />
                <Skeleton className="h-6 w-24" />
                <Skeleton className="h-5 w-16" />
              </div>
            ))}
          </div>
        ) : recent.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <p className="font-display text-lg font-semibold">No conversions yet</p>
            <p className="mt-1 text-sm text-muted">Add your first file to get started.</p>
            <Link to="/app/convert" className="mt-4 inline-block">
              <Button>Convert a file</Button>
            </Link>
          </div>
        ) : (
          <ul className="divide-y divide-outline">
            {recent.map((job, i) => (
              <motion.li
                key={job.job_id}
                className={DASHBOARD_ROW_GRID}
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.1 + i * 0.05, duration: 0.3 }}
              >
                <span className="min-w-0 truncate text-sm text-on-background">
                  {job.fileName ?? job.input_file}
                </span>
                <FormatMorph from={job.source_format} to={job.target_format} size="sm" />
                <StatusBadge status={job.status} />
                <span className="hidden text-right font-mono text-xs text-muted sm:block">
                  {formatDateTime(job.createdAt)}
                </span>
                <div className="flex items-center justify-end">
                  {job.status === "COMPLETED" && (
                    <button
                      onClick={() => handleDownload(job.job_id)}
                      className="text-muted transition-colors hover:scale-110 hover:text-primary"
                      aria-label="Download"
                    >
                      <Download size={18} />
                    </button>
                  )}
                  {job.status === "FAILED" && <ErrorButton message={job.errorMessage} />}
                </div>
              </motion.li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  footer,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  footer?: React.ReactNode;
}) {
  return (
    <Card hover className="h-full p-5">
      <div className="mb-2 flex items-center gap-2 text-muted">
        <Icon size={18} />
        <span className="text-sm">{label}</span>
      </div>
      <p className="font-display text-3xl font-semibold">{value}</p>
      {footer && <div className="mt-3">{footer}</div>}
    </Card>
  );
}

/** The featured stat: credits get a larger card on the left of the dashboard
 *  grid, with a bigger figure, the date the allowance refreshes, and a direct
 *  route to top up. */
function CreditStatCard({ value, resetAt }: { value: string; resetAt?: string | null }) {
  // Absent on an older API and `null` for tiers without persistent credits —
  // both cases simply omit the line rather than showing a placeholder date.
  const resetLabel = formatDateOrNull(resetAt);
  return (
    <Card hover className="relative flex h-full flex-col overflow-hidden p-6">
      <span
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-gradient-to-br from-primary/10 via-transparent to-transparent"
      />
      <div className="relative mb-2 flex items-center gap-2 text-muted">
        <Coins size={18} />
        <span className="text-sm">Credits remaining</span>
      </div>
      <p className="relative font-display text-5xl font-semibold leading-none tracking-tight tabular-nums sm:text-6xl lg:text-5xl xl:text-6xl">
        {value}
      </p>
      {resetLabel && (
        <p
          className="relative mt-2 text-xs text-muted"
          title={resetAt ? `Resets ${formatDateTime(resetAt)}` : undefined}
        >
          Resets <span className="font-medium text-on-background">{resetLabel}</span>
        </p>
      )}
      <div className="relative mt-auto pt-6">
        <Link
          to="/app/billing"
          className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
        >
          Add credits <ArrowRight size={15} />
        </Link>
      </div>
    </Card>
  );
}
