import { initializeApp, getApps } from "firebase/app";
import { getFirestore } from "firebase/firestore";
import { getStorage } from "firebase/storage";
import { getAuth } from "firebase/auth";

const firebaseConfig = {
  apiKey: "AIzaSyCGvrMelGam51LqlOA_IBhD16wyyzj4FG4",
  authDomain: "looker-studio-pro-msanford.firebaseapp.com",
  projectId: "looker-studio-pro-msanford",
  storageBucket: "looker-studio-pro-msanford.firebasestorage.app",
  messagingSenderId: "1060127284262",
  appId: "1:1060127284262:web:4f1cafd86c0f1d086e9ae7"
};

// Initialize Firebase
const app = getApps().length === 0 ? initializeApp(firebaseConfig) : getApps()[0];
const db = getFirestore(app);
const storage = getStorage(app);
const auth = getAuth(app);

export { app, db, storage, auth };
