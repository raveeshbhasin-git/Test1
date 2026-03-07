#!/usr/bin/env python3
"""
Rock Concert Recommender
Finds the best upcoming rock concerts near you based on your music taste.
Uses Claude with web search to find and rank concerts.
"""

import anthropic
import sys

SYSTEM_PROMPT = """You are a rock music expert and concert scout. Your job is to find
the best upcoming rock concerts near the user's location in the next 12 months, then
rank and recommend them based on the user's music taste.

When searching for concerts:
- Search for rock concerts, shows, and tours in the user's city/region
- Look at multiple sources (Ticketmaster, Live Nation, venue websites, Bandsintown, Songkick)
- Consider all sub-genres: classic rock, alternative, indie rock, metal, punk, hard rock, etc.
- Look for both major arena acts and smaller/mid-size venue shows

When making recommendations:
- Match concerts to the user's stated favorite artists and genres
- Explain WHY each concert is a good match for their taste
- Include practical info: approximate date, venue, and how to find tickets
- Be specific — give actual band names and real upcoming shows
- Rank from best match to least match
- If you find 10+ concerts, highlight the top 5-7 most relevant ones

Format your final recommendations clearly with:
1. Concert name / headlining artist
2. Why it matches their taste
3. When & where (approximate)
4. How to get tickets
"""


def get_user_preferences() -> tuple[str, str]:
    """Collect the user's location and music preferences."""
    print("\n🎸 Rock Concert Recommender 🎸")
    print("=" * 45)
    print("I'll find the best rock concerts near you")
    print("in the next 12 months based on your taste.\n")

    location = input("📍 Your city (e.g. 'Austin, TX' or 'London, UK'): ").strip()
    if not location:
        print("Error: location is required.")
        sys.exit(1)

    print("\nTell me about your music taste:")
    print("(e.g. favorite bands, genres, artists you love)")
    taste = input("🎵 Your taste: ").strip()
    if not taste:
        print("Error: music taste is required.")
        sys.exit(1)

    return location, taste


def find_concerts(location: str, taste: str) -> None:
    """Use Claude with web search to find and recommend concerts."""
    client = anthropic.Anthropic()

    user_prompt = (
        f"I'm looking for rock concerts near {location} in the next 12 months.\n\n"
        f"My music taste: {taste}\n\n"
        f"Please search for upcoming rock concerts in my area and recommend the best "
        f"ones that match my taste. Search multiple sources to find real, upcoming shows."
    )

    print(f"\n🔍 Searching for rock concerts near {location}...")
    print("(This may take a moment while I search the web)\n")

    # Stream the response for a better UX
    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        tools=[
            {"type": "web_search_20260209", "name": "web_search"},
        ],
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        in_thinking = False
        for event in stream:
            if event.type == "content_block_start":
                if event.content_block.type == "thinking":
                    in_thinking = True
                    print("💭 Thinking...", end="", flush=True)
                elif event.content_block.type == "text":
                    if in_thinking:
                        print()  # newline after thinking indicator
                    in_thinking = False
                elif event.content_block.type == "tool_use":
                    in_thinking = False

            elif event.type == "content_block_delta":
                if event.delta.type == "text_delta":
                    print(event.delta.text, end="", flush=True)

            elif event.type == "content_block_stop":
                pass

        print()  # final newline


def main() -> None:
    location, taste = get_user_preferences()
    print("\n" + "=" * 45)
    find_concerts(location, taste)
    print("\n" + "=" * 45)
    print("🎟️  Enjoy the shows! Rock on! 🤘")


if __name__ == "__main__":
    main()
