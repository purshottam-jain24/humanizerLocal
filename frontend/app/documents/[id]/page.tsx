import { PageTransition } from "@/components/motion/transitions";
import { SimpleReview } from "@/components/review/simple-review";

export default async function DocumentPage({ params, searchParams }: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ ai?: string }>;
}) {
  const { id } = await params;
  const { ai } = await searchParams;
  return (
    <PageTransition>
      <SimpleReview docId={id} highlightAi={ai === "1"} />
    </PageTransition>
  );
}
