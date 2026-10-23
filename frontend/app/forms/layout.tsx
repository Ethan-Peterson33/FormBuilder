import Link from "next/link";

export default function FormsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">
              Form Studio
            </h1>

            <p className="text-xs text-slate-500">
              Build and manage dynamic forms
            </p>
          </div>

          <div className="text-sm text-slate-500">
            ImageEndpoint
          </div>
        </div>
      </header>

      <div className="mx-auto flex max-w-7xl">
        <aside className="hidden min-h-[calc(100vh-4rem)] w-64 border-r border-slate-200 bg-white p-4 md:block">
          <nav className="space-y-1">
            <Link
              href="/forms"
              className="block rounded-lg px-3 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-100 hover:text-slate-950"
            >
              Forms
            </Link>

            <Link
              href="/forms/builder"
              className="block rounded-lg px-3 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-100 hover:text-slate-950"
            >
              Form Builder
            </Link>
          </nav>
        </aside>

        <main className="min-w-0 flex-1 p-6 md:p-10">
          {children}
        </main>
      </div>
    </div>
  );
}