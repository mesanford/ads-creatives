import { db } from "@/lib/firebase";
import { collection, addDoc, serverTimestamp } from "firebase/firestore";

const CLOUD_FUNCTION_URLS = {
  meta: "https://us-central1-looker-studio-pro-msanford.cloudfunctions.net/meta-creatives-gallery",
  bing: "https://us-central1-looker-studio-pro-msanford.cloudfunctions.net/bing-creatives-gallery",
  google: "https://us-central1-looker-studio-pro-msanford.cloudfunctions.net/gads-creatives-gallery"
};

export async function triggerPipeline(platform: 'meta' | 'bing' | 'google') {
  try {
    const url = CLOUD_FUNCTION_URLS[platform];
    
    // Log the trigger event in Firestore for audit
    await addDoc(collection(db, "pipeline_logs"), {
      platform,
      action: "manual_trigger",
      timestamp: serverTimestamp(),
      status: "initiated"
    });

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      }
    });

    const result = await response.text();
    
    return { success: response.ok, message: result };
  } catch (error: any) {
    console.error(`Error triggering ${platform} pipeline:`, error);
    return { success: false, message: error.message };
  }
}
