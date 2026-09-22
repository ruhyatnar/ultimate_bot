import React from 'react';

/**
 * Section shell — one consistent header pattern for every dashboard block:
 * colored icon + uppercase title + optional subtitle, arbitrary meta chips in
 * the middle, and action buttons on the right. Keeps the layout scannable and
 * removes the per-card header inconsistencies.
 */
export const Section: React.FC<{
  icon: React.ReactNode;
  iconClass?: string;
  title: string;
  subtitle?: string;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  bodyClass?: string;
}> = ({ icon, iconClass = 'text-amber-400', title, subtitle, meta, actions, children, bodyClass }) => (
  <section className="card overflow-hidden">
    <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-slate-700/60 px-4 py-3">
      <div className="flex items-center gap-2 min-w-0">
        <span className={`shrink-0 ${iconClass}`}>{icon}</span>
        <h2 className="text-sm font-bold text-white truncate">{title}</h2>
        {subtitle && (
          <span className="hidden truncate text-[11px] text-slate-500 md:inline">· {subtitle}</span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {meta}
        {actions}
      </div>
    </header>
    <div className={bodyClass}>{children}</div>
  </section>
);

/** Small labeled stat chip used in section headers (e.g. "Scan 10s", "3 pairs"). */
export const Chip: React.FC<{ label?: string; value: React.ReactNode; title?: string; tone?: 'default' | 'good' | 'warn' | 'bad' }> = ({
  label,
  value,
  title,
  tone = 'default'
}) => {
  const tones = {
    default: 'text-slate-300',
    good: 'text-emerald-300',
    warn: 'text-amber-300',
    bad: 'text-rose-300'
  } as const;
  return (
    <span title={title} className={`num text-[11px] ${tones[tone]}`}>
      {label && <span className="mr-1 font-medium text-slate-500">{label}</span>}
      <strong className="font-semibold">{value}</strong>
    </span>
  );
};
