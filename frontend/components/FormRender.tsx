export type FieldType =
  | "text"
  | "textarea"
  | "number"
  | "date"
  | "select";

export type FieldConfig = {
  id: string;
  type: FieldType;
  label: string;
  required?: boolean;
  placeholder?: string;
  options?: string[];
};
export type FormConfig = {
  id: string;
  name: string;
  description?: string;
  fields: FieldConfig[];
};

type FormRendererProps = {
  config: FormConfig;
};

const inputClass =
  "mt-2 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-slate-400 focus:ring-4 focus:ring-slate-100";

export default function FormRenderer({
  config,
}: FormRendererProps) {

  function renderField(field: FieldConfig) {
    switch (field.type) {
      case "text":
        return (
          <input
            id={field.id}
            name={field.id}
            className={inputClass}
            type="text"
            placeholder={field.placeholder}
            required={field.required}
          />
        );

      case "textarea":
        return (
          <textarea
            id={field.id}
            name={field.id}
              className={`${inputClass} resize-y`}
            placeholder={field.placeholder}
            required={field.required}
          />
        );

      case "number":
        return (
          <input
            id={field.id}
            name={field.id}
            className={inputClass}
            type="number"
            placeholder={field.placeholder}
            required={field.required}
          />
        );

      case "date":
        return (
          <input
            id={field.id}
            name={field.id}
            className={inputClass}
            type="date"
            required={field.required}
          />
        );

      case "select":
        return (
          <select
            id={field.id}
            name={field.id}
            className={inputClass}
            required={field.required}
          >
            <option value="">
              Select an option
            </option>

            {field.options?.map((option) => (
              <option
                key={option}
                value={option}
              >
                {option}
              </option>
            ))}
          </select>
        );

      default:
        return (
          <p>
            Unknown field type: {field.type}
          </p>
        );
    }
  }

  return (
    <form>
      {config.fields.map((field) => (
        <div key={field.id}>
          <label htmlFor={field.id}>
            {field.label}
            {field.required && " *"}
          </label>

          {renderField(field)}
        </div>
      ))}

      <button type="submit">
        Submit
      </button>
    </form>
  );
}