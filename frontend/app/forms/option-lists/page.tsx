import OptionLibrary from "@/components/OptionLibrary";

type PageProps = {
  searchParams: Promise<{ id?: string }>;
};

export const metadata = { title: "Option Library" };

export default async function OptionLibraryPage({ searchParams }: PageProps) {
  const { id } = await searchParams;
  return <OptionLibrary initialSelectedId={id} />;
}
