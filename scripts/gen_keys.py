from mandate import crypto

private_key = crypto.generate_private_key()
print(f"MANDATE_PRIVATE_KEY={crypto.private_key_to_b64(private_key)}")
print(f"MANDATE_PUBLIC_KEY={crypto.public_key_to_b64(private_key.public_key())}")
