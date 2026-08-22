import { FileItem } from '../types/backendTypes';


/**
 * searchLogic.ts
 *
 * Provides parsing and evaluation logic for the advanced file browser search.
 * Supports boolean operators (&&, ||), grouping with parentheses, and property-based filtering.
 */

// Token types
type TokenType = 'AND' | 'OR' | 'LPAREN' | 'RPAREN' | 'CONDITION';

interface Token {
    type: TokenType;
    value: string;
}

/**
 * Splits a raw query string into a list of tokens.
 *
 * @param query The search query string.
 * @returns An array of Token objects.
 */
function tokenize(query: string): Token[] {
    const tokens: Token[] = [];
    let current = 0;

    while (current < query.length) {
        let char = query[current];

        if (/\s/.test(char)) {
            current++;
            continue;
        }

        if (char === '(') {
            tokens.push({ type: 'LPAREN', value: '(' });
            current++;
            continue;
        }

        if (char === ')') {
            tokens.push({ type: 'RPAREN', value: ')' });
            current++;
            continue;
        }

        // Check for && and ||
        if (query.substr(current, 2) === '&&') {
            tokens.push({ type: 'AND', value: '&&' });
            current += 2;
            continue;
        }

        if (query.substr(current, 2) === '||') {
            tokens.push({ type: 'OR', value: '||' });
            current += 2;
            continue;
        }

        // Parse condition OR random text until next special char
        let value = '';

        // We look ahead for special tokens to delimit the condition text
        while (current < query.length) {
            const lookahead = query.substr(current, 2);
            if (lookahead === '&&' || lookahead === '||' || query[current] === '(' || query[current] === ')') {
                break;
            }
            value += query[current];
            current++;
        }

        value = value.trim();
        if (value) {
            // Check if value is just "and" or "or" (case insensitive)
            if (value.toLowerCase() === 'and') {
                tokens.push({ type: 'AND', value: '&&' });
            } else if (value.toLowerCase() === 'or') {
                tokens.push({ type: 'OR', value: '||' });
            } else {
                tokens.push({ type: 'CONDITION', value });
            }
        }
    }
    return tokens;
}

/**
 * Recursive Descent Parser for evaluating search queries against a file item.
 *
 * Grammar:
 * Expr -> Term { OR Term }
 * Term -> Factor { AND Factor }
 * Factor -> ( Expr ) | Condition
 */
class Parser {
    private tokens: Token[];
    private position: number = 0;
    private file: FileItem;

    constructor(tokens: Token[], file: FileItem) {
        this.tokens = tokens;
        this.file = file;
    }

    /**
     * Parses and evaluates the complete expression.
     * @returns True if the file matches the query, False otherwise.
     */
    parse(): boolean {
        return this.parseExpression();
    }

    private peek(): Token | undefined {
        return this.tokens[this.position];
    }

    private consume(): Token | undefined {
        return this.tokens[this.position++];
    }

    private parseExpression(): boolean {
        let left = this.parseTerm();

        while (this.peek()?.type === 'OR') {
            this.consume();
            const right = this.parseTerm();
            left = left || right;
        }

        return left;
    }

    private parseTerm(): boolean {
        let left = this.parseFactor();

        while (this.peek()?.type === 'AND') {
            this.consume();
            const right = this.parseFactor();
            left = left && right;
        }

        return left;
    }

    private parseFactor(): boolean {
        if (this.peek()?.type === 'LPAREN') {
            this.consume();
            const result = this.parseExpression();
            if (this.peek()?.type === 'RPAREN') {
                this.consume();
            }
            // If missing RPAREN, we just return result (lenient parsing)
            return result;
        }

        if (this.peek()?.type === 'CONDITION') {
            const token = this.consume();
            return this.evaluateCondition(token?.value || '');
        }

        // Implicit wildcard/true for empty factors to avoid crashing
        return true;
    }

    private evaluateCondition(condition: string): boolean {
        // Condition format: property operator value
        // Or just text (global search)

        const lowerCondition = condition.toLowerCase();
        // Regex for structured condition
        // operator: includes, excludes, ==, !=, >=, <=, >, <, =, :
        const match = lowerCondition.match(/^([a-z]+)\s*(includes|excludes|==|!=|>=|<=|>|<|=|:)\s*(.+)$/);

        if (match) {
            const [, propertyKey, operator, valueString] = match;

            // Map short property names to FileItem properties
            let fileValueString = '';
            if (propertyKey.startsWith('exp')) fileValueString = String(this.file.exposure || '');
            else if (propertyKey === 'iso') fileValueString = String(this.file.iso || '');
            else if (propertyKey.startsWith('cam')) fileValueString = String(this.file.camera || '');
            else if (propertyKey === 'name' || propertyKey === 'file') fileValueString = String(this.file.name || '');
            else if (propertyKey.startsWith('filt')) fileValueString = String(this.file.filter || '');
            else return false; // Unknown property usually results in no match

            const fileNumericValue = parseFloat(fileValueString);
            const targetNumericValue = parseFloat(valueString);
            const isNumericComparison = !isNaN(targetNumericValue) && !isNaN(fileNumericValue);

            if (operator === ':' || operator === 'includes') return fileValueString.toLowerCase().includes(valueString);
            if (operator === 'excludes') return !fileValueString.toLowerCase().includes(valueString);

            if ((operator === '=' || operator === '==') && !isNumericComparison) {
                return fileValueString.toLowerCase() === valueString;
            }

            if (operator === '==' && isNumericComparison) return fileNumericValue === targetNumericValue;
            if (operator === '!=' && isNumericComparison) return fileNumericValue !== targetNumericValue;
            if (operator === '>' && isNumericComparison) return fileNumericValue > targetNumericValue;
            if (operator === '<' && isNumericComparison) return fileNumericValue < targetNumericValue;
            if (operator === '>=' && isNumericComparison) return fileNumericValue >= targetNumericValue;
            if (operator === '<=' && isNumericComparison) return fileNumericValue <= targetNumericValue;

            return false;
        } else {
            // Fallback to Global search check against common properties
            const name = String(this.file.name || '').toLowerCase();
            const camera = String(this.file.camera || '').toLowerCase();
            const exposure = String(this.file.exposure || '').toLowerCase();
            const filter = String(this.file.filter || '').toLowerCase();
            return (
                name.includes(lowerCondition) ||
                camera.includes(lowerCondition) ||
                exposure.includes(lowerCondition) ||
                filter.includes(lowerCondition)
            );
        }
    }
}

/**
 * Evaluates a search query against a specific file item.
 *
 * @param file The file item to check.
 * @param query The search query string.
 * @returns True if the file matches the query.
 */
export function matchesQuery(file: FileItem, query: string): boolean {
    if (!query || !query.trim()) return true;
    try {
        const tokens = tokenize(query);
        if (tokens.length === 0) return true;
        const parser = new Parser(tokens, file);
        return parser.parse();
    } catch {
        return false; // Fail gracefully on parsing errors
    }
}
