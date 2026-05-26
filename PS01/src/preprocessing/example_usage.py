from src.preprocessing.tokenizer import BankingTokenizer
from src.preprocessing.banking_rules import BankingRules

# Example tokenization
tokenizer = BankingTokenizer()
text = "PAN: ABCDE1234F, Income: 55000 rupees per month"
masked, mapping = tokenizer.tokenize(text)
print(f"Masked: {masked}")
print(f"Mapping: {mapping}")

# Example derived fact
disposable = BankingRules.calculate_disposable_income(55000, 30000, 12000)
print(f"Disposable: {disposable}")
