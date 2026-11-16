import { PageTransition } from "@/components/motion/transitions";
import { SettingsView } from "@/components/settings/settings-view";

export default function SettingsPage() {
  return (
    <PageTransition>
      <SettingsView />
    </PageTransition>
  );
}
