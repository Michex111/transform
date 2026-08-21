import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "motion/react";
import { ArrowRight, Download, Gauge, Coins, HardDrives } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { useJobs } from "@/jobs/JobsContext";
import { Button, Card, FormatMorph, ProgressBar, StatusBadge } from "@/components/ui";
import { Stagger, Item } from "@/lib/motion";
import { formatBytes, formatDateTime } from "@/lib/format";
import type { DashboardResponse } from "@/api/types";

export function DashboardPage() {
  const { user } = useAuth();
  const { jobs } = useJobs();
  const { error } = useToast();
  const [stats, setStats] = useState<DashboardResponse | null>(null);

  useEffect(() => {
    let active = true;
    api
      .dashboard()
      .then((d) => active && setStats(d))
      .catch((e: Error) => error(e.message));
    return () => {
      active = false;
    };
  }, [error]);

  const recent = useMemo(() => jobs.slice(0, 5), [jobs]);

  const total = stats?.conversion_stats.total_jobs ?? jobs.length;
  const completed = stats?.conversion_stats.successful_jobs ?? jobs.filter((j) => j.status === "COMPLETED").length;
  const successRate = total ? Math.round((completed / total) * 100) : 0;
  const storagePct = stats?.storage_stats.used_percent ?? 0;

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

      {/* Stat cards */}
      <Stagger className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Item><StatCard icon={Gauge} label="Total conversions" value={String(total)} /></Item>
        <Item><StatCard icon={Download} label="Success rate" value={`${successRate}%`} /></Item>
        <Item>
          <StatCard
            icon={Coins}
            label="Credits remaining"
            value={stats ? String(stats.credit_balance) : "—"}
          />
        </Item>
        <Item>
          <StatCard
            icon={HardDrives}
            label="Storage used"
            value={stats ? formatBytes(stats.storage_stats.used_bytes) : "—"}
            footer={<ProgressBar value={storagePct} from="var(--color-primary)" />}
          />
        </Item>
      </Stagger>

      {/* Recent conversions */}
      <Card className="overflow-hidden">
        <div className="flex items-center justify-between border-b border-outline px-5 py-4">
          <h2 className="font-display text-lg font-semibold">Recent conversions</h2>
          <Link to="/app/queue" className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">
            View all <ArrowRight size={16} />
          </Link>
        </div>

        {recent.length === 0 ? (
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
                className="flex items-center gap-4 px-5 py-3 transition-colors hover:bg-surface-variant/50"
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.1 + i * 0.05, duration: 0.3 }}
              >
                <span className="min-w-0 flex-1 truncate text-sm text-on-background">
                  {job.fileName ?? job.input_file}
                </span>
                <FormatMorph from={job.source_format} to={job.target_format} size="sm" />
                <StatusBadge status={job.status} />
                <span className="hidden w-32 shrink-0 text-right font-mono text-xs text-muted sm:block">
                  {formatDateTime(job.createdAt)}
                </span>
                {job.status === "COMPLETED" && (
                  <a
                    href={api.getJobDownloadUrl(job.job_id)}
                    className="text-muted transition-colors hover:scale-110 hover:text-primary"
                    aria-label="Download"
                  >
                    <Download size={18} />
                  </a>
                )}
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
