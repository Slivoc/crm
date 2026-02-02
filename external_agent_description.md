# Aerospace Hardware CRM System - External Agent Description

## Overview

This is a comprehensive Customer Relationship Management (CRM) system specifically designed for the aerospace hardware industry. The system manages sales, purchasing, and communications for a company that supplies approved, traceable fasteners, consumables, and hardware to operators, OEMs, and MROs.

## Key Modules

### 1. Salespeople CRM Module

**Core Features:**
- **Customer Management**: Hierarchical customer relationships with parent/child associations
- **Contact Management**: Detailed contact tracking with communication history, status management, and segmentation
- **Activity Tracking**: Comprehensive logging of all customer interactions (phone, email, meetings)
- **Call Lists**: Automated call list management with snoozing, priority assignment, and communication status tracking
- **Monthly Planning**: Sophisticated planner for setting and tracking monthly sales targets with AI-powered suggestions
- **News Monitoring**: Automated collection and delivery of customer news via AI (Perplexity + ChatGPT)
- **Risk Analysis**: Customer churn risk assessment based on purchase patterns and communication frequency
- **Email Integration**: Direct Outlook integration via Microsoft Graph API for seamless communication tracking

**Key Capabilities:**
- Consolidated customer view (handles multiple related companies as single entities)
- Automated communication logging from Outlook emails
- AI-powered email suggestions for outreach
- Customer lifecycle analysis (new business, at-risk, dormant)
- Bulk operations for contact management and communication

### 2. Parts List Module

**Core Features:**
- **Parts List Creation**: AI-powered extraction of part numbers and quantities from emails, PDFs, and Excel files
- **Supplier Quoting**: Multi-supplier quote management with automated price comparison and cost analysis
- **Costing Interface**: Visual costing tool with stock availability, BOM analysis, and supplier selection
- **Sourcing Analysis**: Comprehensive part availability checking across multiple data sources (VQ, PO, Stock, Excess, ILS)
- **Supplier Communication**: Automated email campaigns to suppliers with tracking and follow-up management
- **Quote Extraction**: AI-powered parsing of supplier quotes from various formats (PDF, Excel, email text)

**Key Capabilities:**
- Bulk supplier emailing with status tracking
- Automatic quote availability notifications
- Cost optimization recommendations
- Integration with manufacturer approval databases (QPL)
- Excel export functionality for procurement teams

### 3. Email Integration Module

**Core Features:**
- **Microsoft Graph API Integration**: Full Outlook integration for email sending, receiving, and tracking
- **Email Triage**: AI-powered automatic processing of incoming emails to create parts lists or other records
- **Contact Matching**: Automatic association of email senders with existing customers/suppliers
- **Signature Detection**: Automated extraction and management of email signatures
- **Bulk Email Processing**: High-volume email scanning and contact database population
- **Email Templates**: Template management with dynamic placeholder replacement

**Key Capabilities:**
- Real-time email synchronization with database
- Automatic RFQ creation from customer emails
- Supplier quote processing from email attachments
- Contact database enrichment via email analysis
- Integration with HubSpot for additional CRM functionality

## Technical Architecture

**Backend:**
- Flask web framework with SQLAlchemy ORM
- PostgreSQL database (recently migrated from SQLite)
- Microsoft Graph API for email integration
- OpenAI GPT models for AI-powered features
- Background job processing with APScheduler

**Frontend:**
- Bootstrap-based responsive UI
- Handsontable for spreadsheet-like data editing
- AJAX-powered dynamic interfaces
- Mobile-responsive design

**Key Integrations:**
- Microsoft Outlook/Exchange via Graph API
- HubSpot CRM (optional)
- Perplexity AI for news research
- OpenAI for text processing and email generation

## Data Model Highlights

**Core Entities:**
- Customers (with hierarchical relationships)
- Contacts (with communication history)
- Suppliers and supplier contacts
- Parts lists (with line items and costing)
- Email communications (tracked via Message-ID)
- Sales orders and purchase orders
- Stock movements and inventory
- Manufacturer approvals (QPL data)

**Key Relationships:**
- Customers have multiple contacts
- Parts lists belong to customers and have multiple line items
- Line items link to supplier quotes and communications
- All communications are tracked with direction (inbound/outbound)

## Business Process Flow

1. **Lead Generation**: Email triage automatically creates parts lists from customer inquiries
2. **Parts Research**: AI extracts part requirements and system looks up availability across all data sources
3. **Supplier Outreach**: Bulk emailing to suppliers with quote requests and automated follow-up
4. **Quote Management**: Centralized quote collection and comparison with cost analysis
5. **Customer Communication**: Sales team uses integrated email system with templates and tracking
6. **Order Fulfillment**: Seamless handoff to procurement with costed parts lists

## Performance Optimizations

**Recent Improvements:**
- PostgreSQL migration with optimized indexes
- Bulk quote availability endpoints to reduce database load
- Cached email synchronization
- Consolidated customer queries to handle related companies efficiently
- Background job processing for AI operations

**Key Performance Features:**
- Email caching system with TTL-based expiration
- Batch processing for bulk operations
- Optimized database queries with proper indexing
- Asynchronous AI processing to avoid UI blocking

## Security & Compliance

**Data Protection:**
- Secure API key management
- Email content encryption at rest
- Access control based on user roles
- Audit logging for all communications

**Aerospace-Specific Features:**
- QPL (Qualified Products List) integration
- Dual release certification tracking
- Manufacturer traceability requirements
- Export compliance considerations

## External Agent Integration Points

**Primary APIs:**
- Email webhook endpoints for automated processing
- Parts list creation APIs with AI extraction
- Supplier quote submission endpoints
- Customer communication logging APIs

**Data Exchange:**
- Excel import/export functionality
- PDF processing for supplier quotes
- Email attachment handling
- JSON APIs for external system integration

**Automation Triggers:**
- Email-based RFQ creation
- Supplier quote processing
- Customer news delivery
- Communication logging

This system serves as the central hub for all customer-facing operations in an aerospace hardware business, providing comprehensive visibility and automation for the entire sales and procurement lifecycle.