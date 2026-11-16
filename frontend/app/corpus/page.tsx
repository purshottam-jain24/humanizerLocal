import { PageTransition } from "@/components/motion/transitions";
import { CorpusView } from "@/components/corpus/corpus-view";

export default function CorpusPage() {
  return (
    <PageTransition>
      <CorpusView />
    </PageTransition>
  );
}
