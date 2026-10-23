type ProcessGuideProps = {
  title: string;
  description: string;
  steps: string[];
  updated?: string;
};

export default function ProcessGuide({
  title,
  description,
  steps,
  updated,
}: ProcessGuideProps) {
  return (
    <div className="rounded-2xl border border-blue-100 bg-blue-50/50 p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-blue-600">
            How this works
          </p>

          <h3 className="mt-1 text-lg font-semibold text-slate-950">
            {title}
          </h3>
        </div>

        {updated && (
          <span className="whitespace-nowrap rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-500 shadow-sm">
            Updated {updated}
          </span>
        )}
      </div>

      <p className="mt-3 text-sm leading-6 text-slate-600">
        {description}
      </p>

      <div className="mt-5 space-y-3">
        {steps.map((step, index) => (
          <div
            key={step}
            className="flex gap-3"
          >
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-600 text-xs font-semibold text-white">
              {index + 1}
            </div>

            <p className="pt-0.5 text-sm text-slate-700">
              {step}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}