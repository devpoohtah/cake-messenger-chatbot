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
