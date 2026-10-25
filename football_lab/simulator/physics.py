import math
from football_lab.game.state import Vec


def clamp_position(pos):
    return Vec(max(-55, min(55, pos.x)), max(-35, min(35, pos.y)))


def move_toward(start, target, distance):
    length = start.distance(target)
    if length < 1e-9:
        return start
    ratio = min(1, distance / length)
    return Vec(start.x + (target.x - start.x) * ratio, start.y + (target.y - start.y) * ratio)


def segment_distance(point, a, b):
    dx, dy = b.x - a.x, b.y - a.y
    length2 = dx * dx + dy * dy
    t = max(0, min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / length2)) if length2 else 0
    return point.distance(Vec(a.x + t * dx, a.y + t * dy)), t


def circle_contact(point, a, b, radius):
    """Closest approach and first contact time on the segment, or None if it misses."""
    distance, closest = segment_distance(point, a, b)
    if distance > radius:
        return None
    dx, dy = b.x - a.x, b.y - a.y
    length2 = dx * dx + dy * dy
    ox, oy = a.x - point.x, a.y - point.y
    c = ox * ox + oy * oy - radius * radius
    if c <= 0:
        return distance, 0.0
    if length2 < 1e-18:
        return None
    dot = ox * dx + oy * dy
    discriminant = dot * dot - length2 * c
    if discriminant < 0:
        return None
    entry = (-dot - math.sqrt(discriminant)) / length2
    return (distance, entry) if 0 <= entry <= 1 else None


def first_boundary(a, b):
    """First pitch boundary reached; ignores boundaries the ball is moving away from."""
    crossings = []
    for axis, limit in (("x", 55), ("y", 35)):
        start, end = getattr(a, axis), getattr(b, axis)
        delta = end - start
        for line in (-limit, limit):
            if delta and ((line > 0 and delta > 0 and end >= line) or
                          (line < 0 and delta < 0 and end <= line)):
                fraction = (line - start) / delta
                if 0 <= fraction <= 1:
                    crossings.append((fraction, axis, line))
    return min(crossings) if crossings else None


def separate_players(players, radius, iterations=8):
    """Resolve overlap symmetrically, in team/id order, without drawing random numbers."""
    ordered = sorted(players, key=lambda p: p.key)
    diameter = radius * 2
    for _ in range(iterations):
        overlap = False
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                dx, dy = b.position.x - a.position.x, b.position.y - a.position.y
                distance = math.hypot(dx, dy)
                if distance >= diameter - 1e-8:
                    continue
                overlap = True
                if distance < 1e-9:
                    # Even exactly coincident centers need a reproducible, non-team-biased direction.
                    angle = ((a.team_id * 5 + a.player_id) * 17 + (b.team_id * 5 + b.player_id) * 31) * 2.399963
                    nx, ny = math.cos(angle), math.sin(angle)
                else:
                    nx, ny = dx / distance, dy / distance
                shift = (diameter - distance + 1e-8) / 2
                a.position = clamp_position(Vec(a.position.x - nx * shift, a.position.y - ny * shift))
                b.position = clamp_position(Vec(b.position.x + nx * shift, b.position.y + ny * shift))
        if not overlap:
            break
