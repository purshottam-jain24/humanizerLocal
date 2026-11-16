import { PageTransition } from "@/components/motion/transitions";
import { ScanExperience } from "@/components/scanning/scan-experience";

export default async function ScanPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <PageTransition>
      <ScanExperience docId={id} />
    </PageTransition>
  );
}
