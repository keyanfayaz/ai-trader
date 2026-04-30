import type { ReactNode } from "react";

import { Sidebar } from "@/components/sidebar";
import { StartupResumeDialog } from "@/components/startup-resume-dialog";

export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <div className="dashboard-root">
      <StartupResumeDialog />
      <Sidebar />
      <main className="dashboard-content">{children}</main>
    </div>
  );
}
