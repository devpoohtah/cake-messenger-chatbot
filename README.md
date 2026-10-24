# Mrs Brave's Cake - AI Messenger Ordering Automation

BAM 255 Business Analysis for IT project for **Mrs Brave's Cake**, designed to automate customer inquiries and ordering through the business's Facebook Messenger page.

## What It Does

The system uses AI to assist customers through Messenger by:

- Answering basic business and product-related questions
- Providing available information and pricing
- Recognizing when a customer wants to place an order
- Collecting the required order details
- Showing the customer an order summary
- Requiring explicit confirmation before creating the order
- Saving confirmed orders to the database

The AI handles the conversation, while the backend validates the information before it is stored.

## General Flow

    Customer
       |
       v
    Facebook Messenger
       |
       v
    AI + FastAPI Backend
       |
       v
    Order Validation
       |
       v
    Customer Confirmation
       |
       v
    Supabase PostgreSQL

## Main Components

- **Facebook Messenger** - Customer communication
- **AI** - Handles conversation and interprets customer intent
- **FastAPI** - Backend and order processing
- **Supabase PostgreSQL** - Stores customers, business information, and orders
- **Meta Messenger API** - Connects the system to Facebook Messenger

## Limitations

- Depends on Meta Messenger API availability, permissions, and platform policies.
- Public customer access may require Meta App Review and additional permissions.
- AI responses may require validation and are not guaranteed to be perfect.
- Requires an internet connection for Messenger, AI services, and Supabase.
- The system focuses on customer inquiries and order processing and does not replace full accounting, inventory, delivery, or business management software.

## Project Goal

The project aims to reduce repetitive manual work when handling customer inquiries and orders while providing customers with a faster and more convenient way to interact with Mrs Brave's Cake through Facebook Messenger.

## Deployment Guide

### 1. Clone the Repository
```bash
git clone https://github.com/devpoohtah/cake-messenger-chatbot.git
cd cake-messenger-chatbot
```

### 2. Install Dependencies
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Create a `.env` file in the project root and add the required credentials for Supabase, Gemini AI, and Meta Messenger.

### 4. Run the Backend
```bash
uvicorn app.main:app --reload --port 8000
```

### 5. Deploy the Backend and Configure Meta Messenger
1. Deploy the FastAPI backend to a hosting service that supports Python and provides a public HTTPS URL.
2. Configure the Messenger webhook callback URL and verification token in the Meta Developer Dashboard.
3. Subscribe the Facebook Page to the required webhook events.
4. Confirm that the webhook verification succeeds.

### 6. Complete Meta App Review
1. Configure the required app details and privacy policy URL.
2. Request the necessary permissions and Advanced Access, if required.
3. Submit the app for Meta App Review and complete any additional verification requirements that apply.
4. After approval, switch the app to Live mode if required for your intended access.

*Note: During development, the app can remain in Development Mode for testing with accounts assigned an app role. App Review requirements depend on the permissions and access needed.*

### 7. Test the System
Send a message to the Facebook Page and verify that the chatbot responds correctly and confirmed orders are saved to Supabase.
