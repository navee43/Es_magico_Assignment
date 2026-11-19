import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { BarChart3, ClipboardCheck, Files, LayoutDashboard, LogOut, Menu, Upload, X } from "lucide-react";
import { useConfig, useStats } from "../hooks/useApi";
import { tokenStore } from "../services/api";
import { MockBanner } from "./ui";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/upload", label: "Upload", icon: Upload },
  { to: "/documents", label: "Documents", icon: Files },
  { to: "/review", label: "Review queue", icon: ClipboardCheck, badge: true },
  { to: "/analytics", label: "Evaluation", icon: BarChart3 },
];

export function Layout({ onSignOut }: { onSignOut: () => void }) {
  const config = useConfig();
  const stats = useStats();
  const [open, setOpen] = useState(false);
  const pending = stats.data?.needs_review ?? 0;

  const nav = (
    <nav className="flex flex-col gap-0.5" aria-label="Main">
      {NAV.map(({ to, label, icon: Icon, end, badge }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={() => setOpen(false)}
          className={({ isActive }) =>
            `flex items-center gap-3 rounded-md px-3 py-2 text-[14.5px] font-medium transition-colors ${
              isActive ? "bg-white/12 text-white" : "text-white/70 hover:bg-white/6 hover:text-white"
            }`
          }
        >
          <Icon className="size-[18px]" aria-hidden />
          <span className="flex-1">{label}</span>
          {badge && pending > 0 && (
            <span className="num rounded-full bg-[#e7b24f] px-2 text-[12px] font-bold text-ink" aria-label={`${pending} waiting`}>
              {pending}
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  );

  const footer = (
    <div className="space-y-3 border-t border-white/10 pt-4 text-[12.5px] text-white/60">
      {config.data && (
        <p>
          Model <span className="text-white/85">{config.data.llm_model}</span>
          <br />
          Routing <span className="text-white/85">{config.data.routing_mode}</span>, prompt {config.data.prompt_version}
        </p>
      )}
      {config.data?.auth_enabled && (
        <button
          className="flex items-center gap-2 text-white/70 hover:text-white"
          onClick={() => {
            tokenStore.clear();
            onSignOut();
          }}
        >
          <LogOut className="size-4" aria-hidden /> Sign out
        </button>
      )}
    </div>
  );

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[232px_1fr]">
      <aside className="hidden flex-col justify-between bg-ink px-4 py-5 lg:sticky lg:top-0 lg:flex lg:h-screen">
        <div>
          <Brand />
          <div className="mt-8">{nav}</div>
        </div>
        {footer}
      </aside>

      <div className="flex items-center justify-between bg-ink px-4 py-3 lg:hidden">
        <Brand />
        <button className="rounded p-1.5 text-white" onClick={() => setOpen((o) => !o)} aria-label={open ? "Close menu" : "Open menu"} aria-expanded={open}>
          {open ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>
      </div>
      {open && (
        <div className="space-y-4 bg-ink px-4 pb-4 lg:hidden">
          {nav}
          {footer}
        </div>
      )}

      <div className="min-w-0">
        {config.data?.llm_provider === "mock" && <MockBanner />}
        <main className="mx-auto max-w-[1440px] px-5 py-7 sm:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-1 text-white">
      <img src="/favicon.svg" alt="" className="size-7" />
      <div className="leading-tight">
        <p className="text-[15px] font-semibold">Invoice AI</p>
        <p className="text-[12px] text-white/55">Extraction desk</p>
      </div>
    </div>
  );
}
