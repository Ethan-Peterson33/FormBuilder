/**
 * Browser-side client for the reusable option list API. Kept separate from the
 * components so it can be mocked in tests and swapped if the transport changes.
 */

import type { ApiError, OptionList, OptionListInput } from "@/lib/types";

async function parse<T>(response: Response): Promise<T> {
  const payload = (await response.json().catch(() => null)) as T | ApiError | null;
  if (!response.ok) {
    const message =
      payload && typeof payload === "object" && "error" in payload
        ? (payload as ApiError).error
        : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return payload as T;
}

export async function fetchOptionLists(): Promise<OptionList[]> {
  return parse<OptionList[]>(await fetch("/api/option-lists", { cache: "no-store" }));
}

export async function createOptionList(input: OptionListInput): Promise<OptionList> {
  return parse<OptionList>(
    await fetch("/api/option-lists", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    })
  );
}

export async function updateOptionList(id: string, input: OptionListInput): Promise<OptionList> {
  return parse<OptionList>(
    await fetch(`/api/option-lists/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    })
  );
}

export async function deleteOptionList(id: string): Promise<void> {
  const response = await fetch(`/api/option-lists/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!response.ok && response.status !== 404) {
    await parse<void>(response);
  }
}
