import { PageTransition } from "@/components/motion/transitions";
import { QualityCenter } from "@/components/analysis/quality-center";

export default async function AnalysisPage({ params, searchParams }: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ section?: string }>;
}) {
  const { id } = await params;
  const { section } = await searchParams;
  return (
    <PageTransition>
      <QualityCenter docId={id} initialSection={section} />
    </PageTransition>
  );
}
