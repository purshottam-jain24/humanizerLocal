import { PageTransition } from "@/components/motion/transitions";
import { ExportView } from "@/components/export/export-view";

export default async function ExportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <PageTransition>
      <ExportView docId={id} />
    </PageTransition>
  );
}
