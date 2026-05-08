import { db } from "@/lib/firebase";
import { collection, addDoc, serverTimestamp } from "firebase/firestore";

const CLOUD_FUNCTION_URLS = {
  meta: "https://us-central1-looker-studio-pro-msanford.cloudfunctions.net/meta-creatives-gallery",
  bing: "https://us-central1-looker-studio-pro-msanford.cloudfunctions.net/bing-creatives-gallery",
  google: "https://us-central1-looker-studio-pro-msanford.cloudfunctions.net/gads-creatives-gallery"
};

export async function triggerPipeline(platform: 'meta' | 'bing' | 'google') {
  const url = CLOUD_FUNCTION_URLS[platform];

  await addDoc(collection(db, "pipeline_logs"), {
    platform,
    action: "manual_trigger",
    timestamp: serverTimestamp(),
    status: "initiated"
  });

  // Fire-and-forget: pipeline runs for several minutes; don't block the UI waiting for it.
  // The gallery updates live via Firestore onSnapshot as creatives are written.
  fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' } })
    .then(res => console.log(`[${platform}] pipeline response: ${res.status}`))
    .catch(err => console.error(`[${platform}] pipeline error:`, err));

  return { success: true, message: `${platform} sync started. The gallery will update automatically as creatives are written.` };
}
