To integrate **Google authentication** with **Supabase** and **FastAPI** (while using **Supabase Auth** for the backend), I’ll walk you through the process and provide **best practices** for **production-grade** deployment.

---

### **Overview of the Flow**

1. **Frontend (Next.js)**:

   * User clicks on "Sign in with Google" on your frontend.
   * Google sends an **OAuth token** (JWT) back to your frontend.
   * Frontend sends the **JWT token** to the **FastAPI backend** for verification.
2. **Backend (FastAPI)**:

   * FastAPI will verify the token with **Supabase** (via **Supabase's API**).
   * Supabase will return user data (e.g., name, email, etc.).
   * Your FastAPI backend will store the user information in the **Supabase database** and create a **local session** if needed.
3. **Supabase**:

   * **Supabase Auth** handles the authentication part (OAuth2 with Google).
   * **Supabase DB** stores the user data (name, email, etc.).

---

### **1. Frontend (Next.js)**: Google Auth with Supabase

**Step 1.1**: Install Supabase Auth SDK in your Next.js project:

```bash
npm install @supabase/supabase-js
```

**Step 1.2**: Initialize Supabase in your Next.js project:

```javascript
import { createClient } from '@supabase/supabase-js';

const supabase = createClient('https://xyzcompany.supabase.co', 'public-anon-key');
```

**Step 1.3**: Implement Google login on the frontend (using Supabase’s built-in method):

```javascript
// Example function to handle Google login
const signInWithGoogle = async () => {
  const { user, session, error } = await supabase.auth.signIn({
    provider: 'google',
  });

  if (error) {
    console.error('Error during Google login:', error.message);
    return;
  }

  // Send the session's access_token to the backend for validation
  const accessToken = session.access_token;

  // Send access token to FastAPI backend for verification
  const response = await fetch('/api/verify-token', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ accessToken }),
  });

  const data = await response.json();
  console.log('Backend Response:', data);
};
```

This will trigger the **Google OAuth** flow and handle authentication on the frontend.

---

### **2. Backend (FastAPI)**: Verify Google Token with Supabase

You can **reuse** your existing **register API** but with modifications to handle the **Google authentication token**.

**Step 2.1**: Install `httpx` and `python-dotenv` for sending HTTP requests to Supabase and handling environment variables.

```bash
pip install httpx python-dotenv
```

**Step 2.2**: Define a **FastAPI route** to verify the **Google JWT** with Supabase.

* You will send the **JWT** token to Supabase's API for validation.
* Supabase will return the **user data** if the token is valid.

In **FastAPI**, use **HTTP client (httpx)** to verify the token with Supabase.

```python
import httpx
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

# FastAPI app instance
app = FastAPI()

# Define the data model for request body
class VerifyTokenRequest(BaseModel):
    accessToken: str

# API key for Supabase (this should be set as an environment variable or in .env)
SUPABASE_API_URL = "https://xyzcompany.supabase.co/auth/v1/token"
SUPABASE_SECRET_KEY = "your-supabase-service-key"

@app.post("/api/verify-token")
async def verify_token(request: VerifyTokenRequest):
    access_token = request.accessToken
    
    # Send the token to Supabase Auth API for verification
    async with httpx.AsyncClient() as client:
        response = await client.post(
            SUPABASE_API_URL,
            headers={"Authorization": f"Bearer {SUPABASE_SECRET_KEY}"},
            json={"access_token": access_token}
        )

    # If the token is invalid, Supabase will return a 401 status
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Parse user data from Supabase's response
    user_data = response.json()
    
    # Check if user exists in the database and store user data
    # For now, we assume the response contains 'user' data
    user = {
        "email": user_data["email"],
        "user_name": user_data["user_name"],
        "created_at": user_data["created_at"]
    }

    # Here, you can either:
    # - Store user info in your Supabase DB
    # - Or handle it differently based on your needs
    # You might want to check if the user exists and create a new entry or update it.
    
    # Example: Storing user data in the database (simplified)
    # db_session.add(user_data)
    # db_session.commit()

    return {"status": "success", "user_data": user}
```

### **3. Verify the User’s JWT Token in Supabase**

In the backend route `/api/verify-token`, you send the **Google authentication token** to the **Supabase Auth** API for validation.

#### **Verify Token Steps**:

1. **Send the Access Token**: The token is passed to Supabase to verify the user’s identity.
2. **Supabase Validates Token**: Supabase checks if the token is valid.
3. **Get User Data**: If valid, Supabase returns **user details** (email, user ID, etc.).
4. **Store User Info**: You store this user data in the Supabase database.

---

### **4. Handling the User in Supabase Database**

Since **Supabase Auth** is already integrated, you don't need to manage passwords or manual user registration.

* **Supabase automatically creates users** once they authenticate via Google (or any other provider).
* If needed, you can store extra **user profile information** or create custom logic for **updating user data** in your **Supabase database**.

**Storing User Info Example**:

In **FastAPI**, after token validation, you might add or update the user profile:

```python
# Simplified code to insert user data into Supabase DB

from sqlmodel import Session, select

def store_user_in_db(db_session: Session, user_data: dict):
    # Assuming 'User' is a model defined using SQLModel for Supabase DB
    user = User(**user_data)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user
```

---

### **5. Final Considerations**

1. **Security**: Ensure you handle sensitive data securely (e.g., **API keys**, **JWT tokens**).

   * Store **Supabase service keys** and **secrets** in **`.env`** and use `python-dotenv`.
2. **Error Handling**: Handle errors gracefully when verifying tokens (e.g., expired tokens, invalid tokens).
3. **Token Expiry**: The access token from **Google** has an expiry time. Ensure proper handling of **expired tokens** and **refresh tokens** if needed.
4. **User Profile Management**: After authentication, you can expand your user profile management by storing more user-specific data in Supabase (e.g., preferences, subscription status).

---

### **6. Conclusion**

To **handle Google authentication** with **Supabase Auth** in your **FastAPI backend**, follow these steps:

* Use the **Supabase Auth** API for token validation.
* **Validate the token** from the frontend (Next.js) in FastAPI.
* **Store user data** in the **Supabase database** (auto-created by Supabase).
* Ensure that **Google OAuth** and **Supabase Auth** are correctly configured in both **Frontend (Next.js)** and **Backend (FastAPI)**.

This setup allows you to **seamlessly handle authentication** and **store user data**, utilizing Supabase as the central authentication service.

Let me know if you need further clarifications!
