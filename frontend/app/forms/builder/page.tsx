import FormBuilder from "@/components/FormBuilder";
import ProcessGuide from "@/components/ProcessGuide";

export default function FormBuilderPage() {
  return (
    <div className="space-y-8">
      <div>
        <p className="text-sm font-medium text-slate-500">
          Forms
        </p>

        <h2 className="mt-1 text-3xl font-semibold tracking-tight text-slate-950">
          Form Builder
        </h2>
<ProcessGuide
  title="Building configuration instead of HTML"
  description="The builder does not directly create the final form markup. It constructs a FormConfig object that the same FormRenderer can immediately preview."
  updated="today"
  steps={[
    "Form name, description, and fields are stored in React state.",
    "That state is assembled into a FormConfig object.",
    "The FormConfig is passed to FormRenderer for the live preview.",
    "Later, Save Form will send the same configuration to FastAPI for persistence.",
  ]}
/>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
          Build a dynamic form, configure its fields, and preview the result in
          real time.
        </p>
      </div>

      <FormBuilder />
    </div>
  );
}