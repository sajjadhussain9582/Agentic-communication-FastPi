"""Seed conversational Knowledge Base entries — 20 sales-smart entries."""

import json
from sqlmodel import Session, select
from app.core.database import engine
from app.models.knowledge_base import KnowledgeBaseEntry
from app.services.kb_rag import ensure_entry_embedding

SEED_DATA = [
    # ──────────────── website_guidance ────────────────
    {
        "question": "I'm not sure if I need a website or an app.",
        "answer": "If your goal is to build an online presence, showcase services, and capture leads, a website is the quickest and most cost-effective solution. If you need complex features like user dashboards, push notifications, or custom internal workflows, you likely need a web or mobile app platform.",
        "category": "website_guidance",
        "keywords": ["website", "app", "difference", "choose", "guidance"],
        "intent_type": "service_inquiry",
        "role_type": "unknown",
        "priority": 10,
        "tags": ["discovery"]
    },
    {
        "question": "Do I need a full app or just a website?",
        "answer": "It depends on your goals. If you mainly need to attract visitors, share information, and collect inquiries, a website is usually enough. If your users need to log in, manage data, track progress, or interact with real-time features, then an app or web platform is the better fit. We can help you decide during a quick discovery call.",
        "category": "website_guidance",
        "keywords": ["full app", "just a website", "need", "enough"],
        "intent_type": "service_inquiry",
        "role_type": "unknown",
        "priority": 8,
        "tags": ["discovery", "scoping"]
    },

    # ──────────────── mvp_guidance ────────────────
    {
        "question": "What can be included in an MVP?",
        "answer": "A Minimum Viable Product (MVP) includes only the core features necessary to solve your primary problem or test your idea with real users. It typically avoids secondary features—focusing on the essential user journey, basic authentication, and one or two key functionalities to get to market within 4-8 weeks.",
        "category": "mvp_guidance",
        "keywords": ["mvp", "included", "features", "minimum viable product"],
        "intent_type": "service_inquiry",
        "role_type": "unknown",
        "priority": 10,
        "tags": ["scoping"]
    },
    {
        "question": "What does a basic MVP include?",
        "answer": "A basic MVP typically includes: user registration/login, one core workflow (e.g., listing, booking, or ordering), a simple admin dashboard, and responsive design. It deliberately excludes advanced analytics, third-party integrations, and multi-role permissions—those come in later phases.",
        "category": "mvp_guidance",
        "keywords": ["basic mvp", "include", "what", "features"],
        "intent_type": "service_inquiry",
        "role_type": "unknown",
        "priority": 9,
        "tags": ["scoping", "features"]
    },
    {
        "question": "Can we start small and scale later?",
        "answer": "Absolutely. That is actually the recommended approach. We build a focused MVP first so you can validate your idea with real users and real feedback. Once you have traction, we layer in advanced features, integrations, and scaling infrastructure in planned phases. This reduces risk and keeps costs controlled.",
        "category": "mvp_guidance",
        "keywords": ["start small", "scale", "later", "phased", "grow"],
        "intent_type": "service_inquiry",
        "role_type": "unknown",
        "priority": 9,
        "tags": ["scoping", "phased"]
    },

    # ──────────────── real_estate_platform ────────────────
    {
        "question": "How long does a real estate MVP usually take?",
        "answer": "A standard real estate platform MVP—including property listings, basic search/filtering, and contact forms—typically takes 6 to 10 weeks to build and launch, depending on whether it requires complex integrations with MLS databases or CRM systems.",
        "category": "real_estate_platform",
        "keywords": ["real estate", "mvp", "timeline", "how long", "duration"],
        "intent_type": "service_inquiry",
        "role_type": "agent",
        "priority": 10,
        "tags": ["real_estate", "timeline"]
    },
    {
        "question": "What does a real estate MVP include?",
        "answer": "A real estate MVP typically includes property listing pages with photos and details, search and filter functionality (by location, price, type), a contact or inquiry form, an admin panel to manage listings, and responsive mobile-friendly design. Advanced features like map integration, virtual tours, or MLS syncing are usually added in Phase 2.",
        "category": "real_estate_platform",
        "keywords": ["real estate", "mvp", "include", "features", "listings"],
        "intent_type": "service_inquiry",
        "role_type": "agent",
        "priority": 9,
        "tags": ["real_estate", "features"]
    },

    # ──────────────── pricing ────────────────
    {
        "question": "How much does an MVP usually cost?",
        "answer": "MVP costs vary based on scope, but a typical range is $5,000–$15,000 for a focused, single-workflow product. More complex platforms with multiple user roles, third-party integrations, or real-time features may range from $15,000–$40,000. We always provide a detailed estimate after understanding your specific requirements during a consultation.",
        "category": "pricing",
        "keywords": ["cost", "price", "how much", "mvp", "budget"],
        "intent_type": "pricing_request",
        "role_type": "unknown",
        "priority": 10,
        "tags": ["pricing", "budget"]
    },
    {
        "question": "How is pricing affected by scope?",
        "answer": "Pricing is directly tied to scope. More features, more user roles, and more integrations mean more development time and cost. A simple landing page with a contact form is far less than a multi-vendor marketplace with payments. That is why we always recommend defining 'Must Have' vs 'Nice to Have' features first—so the budget goes toward what matters most.",
        "category": "pricing",
        "keywords": ["pricing", "scope", "features", "cost", "affected"],
        "intent_type": "pricing_request",
        "role_type": "unknown",
        "priority": 8,
        "tags": ["pricing", "scoping"]
    },

    # ──────────────── budget_flexibility ────────────────
    {
        "question": "Can the budget be flexible?",
        "answer": "Yes, budget flexibility depends heavily on scope. We typically align on a 'Must Have' vs 'Nice to Have' list. If the budget is strict, we recommend launching a smaller phased MVP first. If the required features exceed the immediate budget, we can map out a multi-phase delivery plan.",
        "category": "budget_flexibility",
        "keywords": ["budget", "flexible", "cost", "price", "too high"],
        "intent_type": "pricing_request",
        "role_type": "unknown",
        "priority": 10,
        "tags": ["pricing"]
    },

    # ──────────────── timeline ────────────────
    {
        "question": "How long does an MVP usually take?",
        "answer": "Most MVPs take 4 to 10 weeks depending on complexity. A simple website or landing page can be done in 2-3 weeks. A web app with user accounts and one core workflow typically takes 6-8 weeks. A platform with multiple roles, payments, or third-party integrations usually takes 8-12 weeks.",
        "category": "timeline",
        "keywords": ["how long", "timeline", "duration", "weeks", "mvp"],
        "intent_type": "service_inquiry",
        "role_type": "unknown",
        "priority": 10,
        "tags": ["timeline"]
    },

    # ──────────────── feature_suggestions ────────────────
    {
        "question": "I don't know the features yet. Can you guide me?",
        "answer": "Absolutely. Our typical process starts with a Discovery Consultation. You tell us your business goals and the problems you want to solve, and our architects will propose the exact features and technical roadmap needed to achieve them. We don't expect you to have a full technical spec up front.",
        "category": "feature_suggestions",
        "keywords": ["don't know", "features", "guide me", "help", "suggestions"],
        "intent_type": "service_inquiry",
        "role_type": "unknown",
        "priority": 10,
        "tags": ["discovery"]
    },

    # ──────────────── consultation ────────────────
    {
        "question": "How does consultation work?",
        "answer": "Our consultation is a focused 30-minute call where we learn about your goals, challenges, and timeline. Based on that, we provide an initial recommendation on approach, technology stack, and rough scope. There is no obligation—it is simply a chance to explore whether we are a good fit to work together.",
        "category": "consultation",
        "keywords": ["consultation", "how", "work", "call", "meeting"],
        "intent_type": "booking_request",
        "role_type": "unknown",
        "priority": 10,
        "tags": ["booking", "process"]
    },
    {
        "question": "How do I book a consultation?",
        "answer": "You can book a free consultation directly through our scheduling link. Just pick a time that works for you, and one of our team members will reach out to confirm. If you prefer, you can also reply here with your preferred date and time and we will set it up for you.",
        "category": "consultation",
        "keywords": ["book", "consultation", "schedule", "meeting", "call"],
        "intent_type": "booking_request",
        "role_type": "unknown",
        "priority": 9,
        "tags": ["booking"]
    },

    # ──────────────── qualification ────────────────
    {
        "question": "What information is needed before a proposal?",
        "answer": "To prepare a meaningful proposal, we typically need: a description of the problem you are solving, your target users, the core features you envision, your budget range (even a rough one helps), your ideal timeline, and any existing assets like wireframes, designs, or competitor references. If you do not have all of this yet, our discovery call will help clarify it.",
        "category": "qualification",
        "keywords": ["information", "needed", "proposal", "before", "requirements"],
        "intent_type": "service_inquiry",
        "role_type": "unknown",
        "priority": 9,
        "tags": ["qualification", "process"]
    },

    # ──────────────── process ────────────────
    {
        "question": "What happens after discovery?",
        "answer": "After the discovery call, we prepare a detailed scope document and project estimate within 2-3 business days. This includes a feature breakdown, timeline, cost estimate, and recommended technology stack. Once you approve, we kick off with a design sprint, followed by iterative development with weekly check-ins.",
        "category": "process",
        "keywords": ["after discovery", "next steps", "what happens", "process"],
        "intent_type": "service_inquiry",
        "role_type": "unknown",
        "priority": 8,
        "tags": ["process"]
    },
    {
        "question": "What is your process from start to finish?",
        "answer": "Our process follows five stages: (1) Discovery—understanding your goals and requirements, (2) Proposal—detailed scope, timeline, and cost estimate, (3) Design—wireframes and UI/UX design, (4) Development—iterative build with weekly demos, (5) Launch & Support—deployment, QA, and ongoing maintenance. Each stage has clear deliverables and approval checkpoints.",
        "category": "process",
        "keywords": ["process", "start to finish", "how", "stages", "workflow"],
        "intent_type": "service_inquiry",
        "role_type": "unknown",
        "priority": 9,
        "tags": ["process"]
    },

    # ──────────────── partnership ────────────────
    {
        "question": "How do partnerships work?",
        "answer": "We partner with contractors, real estate agents, architects, developers, and builders. As a partner, we refer vetted leads directly to you based on project fit and location. We handle the qualification and initial vetting so you receive only serious, pre-qualified opportunities. Partnership is free—we earn through successful project facilitation.",
        "category": "partnership",
        "keywords": ["partnership", "how", "work", "partner", "collaborate"],
        "intent_type": "partnership_inquiry",
        "role_type": "contractor",
        "priority": 9,
        "tags": ["partnership"]
    },
    {
        "question": "How do referrals work?",
        "answer": "Our referral program connects qualified leads with vetted professionals. When a client comes to us with a project, we match them with the right partner based on expertise, availability, and location. Partners receive a warm introduction and project brief. There is no upfront cost—our model is success-based.",
        "category": "partnership",
        "keywords": ["referral", "how", "work", "leads", "program"],
        "intent_type": "partnership_inquiry",
        "role_type": "contractor",
        "priority": 8,
        "tags": ["partnership", "referral"]
    },

    # ──────────────── services_overview ────────────────
    {
        "question": "What services do you offer?",
        "answer": "We connect businesses with vetted professionals across web development, app development, UI/UX design, real estate technology, and digital strategy. Our core offerings include: MVP development, website and platform builds, consultation and discovery workshops, and partner matching for contractors, agents, architects, and builders.",
        "category": "services_overview",
        "keywords": ["services", "offer", "what", "do you do", "provide"],
        "intent_type": "service_inquiry",
        "role_type": "unknown",
        "priority": 10,
        "tags": ["services", "overview"]
    },
]


def run_seed():
    print("Connecting to database...")
    with Session(engine) as session:
        old_entries = session.exec(select(KnowledgeBaseEntry)).all()
        for oe in old_entries:
            session.delete(oe)
        session.commit()

        print(f"Cleared {len(old_entries)} old entries. Inserting {len(SEED_DATA)} new conversational entries...")
        for i, data in enumerate(SEED_DATA, 1):
            entry = KnowledgeBaseEntry(
                question=data["question"],
                answer=data["answer"],
                category=data["category"],
                keywords=data["keywords"],
                intent_type=data.get("intent_type"),
                role_type=data.get("role_type"),
                priority=data.get("priority", 0),
                tags=data.get("tags", []),
            )
            ensure_entry_embedding(session, entry)
            print(f"  [{i}/{len(SEED_DATA)}] Added: {entry.question[:50]}...")

    print("Knowledge Base seeding complete!")


if __name__ == "__main__":
    run_seed()
