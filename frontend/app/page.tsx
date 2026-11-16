import { PageTransition } from "@/components/motion/transitions";
import { Dashboard } from "@/components/dashboard/dashboard";

export default function Home() {
  return (
    <PageTransition>
      <Dashboard />
    </PageTransition>
  );
}
