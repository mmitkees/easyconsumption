import oci
import time

def choose_from_list(items, attr, prompt):
    """
    Presents a numbered list of options for the user to choose from.
    items: list of objects
    attr: attribute of the object to display (e.g. 'display_name')
    prompt: prompt text for input
    Returns: the selected object
    """
    for idx, item in enumerate(items):
        print(f"[{idx}] {getattr(item, attr)}")
    choice = input(f"{prompt} [0-{len(items)-1}] (default 0): ").strip()
    idx = int(choice) if choice.isdigit() and int(choice) < len(items) else 0
    return items[idx]

# === OCI SDK Setup ===
print("Loading OCI configuration and initializing clients...")
config = oci.config.from_file()
identity = oci.identity.IdentityClient(config)
core = oci.core.ComputeClient(config)
network = oci.core.VirtualNetworkClient(config)
print("OCI clients initialized.\n")

# === 1. Get your parent compartment OCID ===
print("Step 1: Choose the compartment where the new compartment will be created.")
use_root = input("Use root compartment? (y/n, default y): ").strip().lower()
if use_root == "n":
    parent_compartment_ocid = input("Enter parent compartment OCID: ").strip()
    if not parent_compartment_ocid.startswith("ocid1.compartment."):
        print("Invalid OCID format! Exiting.")
        exit(1)
    print(f"Using provided compartment OCID: {parent_compartment_ocid}")
else:
    parent_compartment_ocid = config["tenancy"]
    print(f"Using root compartment OCID: {parent_compartment_ocid}")

# === 2. List VCNs and subnets in the parent compartment, and let user pick one ===
print("\nStep 2: Retrieving VCNs in chosen compartment...")
vnets = network.list_vcns(parent_compartment_ocid).data
if not vnets:
    print("No VCNs found in chosen compartment! Exiting.")
    exit(1)
print(f"Found {len(vnets)} VCN(s).")
vcn = choose_from_list(vnets, 'display_name', "Select VCN")
print(f"Selected VCN: {vcn.display_name}")

print("Retrieving subnets in the selected VCN...")
subnets = network.list_subnets(parent_compartment_ocid, vcn_id=vcn.id).data
if not subnets:
    print("No subnets found in selected VCN! Exiting.")
    exit(1)
print(f"Found {len(subnets)} subnet(s).")
subnet = choose_from_list(subnets, 'display_name', "Select Subnet")
subnet_id = subnet.id
print(f"Selected Subnet: {subnet.display_name} (OCID: {subnet_id})")

# === 3. List availability domains and let user pick one ===
print("\nStep 3: Retrieving availability domains...")
tenancy_id = config["tenancy"]
ads = identity.list_availability_domains(tenancy_id).data
print(f"Found {len(ads)} availability domain(s).")
ad = choose_from_list(ads, 'name', "Select Availability Domain")
ad_name = ad.name
print(f"Selected Availability Domain: {ad_name}")

# === 4. List all Windows images for the required shape and pick one ===
print("\nStep 4: Searching for Windows images for shape VM.Standard3.Flex...")
images = core.list_images(
    compartment_id=parent_compartment_ocid,
    operating_system="Windows",
    shape="VM.Standard3.Flex"
).data
if not images:
    print("No Windows images found for this shape! Exiting.")
    exit(1)
print(f"Found {len(images)} Windows image(s). Sorting by most recent.")
images.sort(key=lambda x: x.time_created, reverse=True)
win_image = choose_from_list(images, 'display_name', "Select Windows Image")
win_image_id = win_image.id
print(f"Selected Windows Image: {win_image.display_name} (OCID: {win_image_id})")

# === 5. Create a new compartment named 'tempcomp' under the chosen compartment ===
COMPARTMENT_NAME = 'tempcomp'
COMPARTMENT_DESC = 'Temporary compartment created via script'
INSTANCE_PREFIX = 'auto-win-instance'
INSTANCE_COUNT = 4
SHAPE = 'VM.Standard3.Flex'
OCPUS = 54
MEMORY_GBS = 512
BOOT_VOL_GBS = 50

print(f"\nStep 5: Creating compartment '{COMPARTMENT_NAME}' under chosen compartment...")
compartment = identity.create_compartment(
    oci.identity.models.CreateCompartmentDetails(
        compartment_id=parent_compartment_ocid,
        name=COMPARTMENT_NAME,
        description=COMPARTMENT_DESC
    )
).data
compartment_id = compartment.id
print(f"Compartment '{COMPARTMENT_NAME}' created with OCID: {compartment_id}")

# Wait until the compartment becomes ACTIVE
print("Waiting for compartment to become ACTIVE...")
for i in range(30):
    comp = identity.get_compartment(compartment_id).data
    if comp.lifecycle_state == 'ACTIVE':
        print("Compartment is ACTIVE.")
        break
    time.sleep(2)
else:
    print("Compartment did not become ACTIVE in time. Exiting.")
    exit(1)

# === 6. Launch the compute instances in the new compartment ===
print(f"\nStep 6: Launching {INSTANCE_COUNT} Windows compute instances in compartment '{COMPARTMENT_NAME}'...")
for idx in range(INSTANCE_COUNT):
    display_name = f"{INSTANCE_PREFIX}-{idx + 1}"
    print(f"\nLaunching instance {idx+1}/{INSTANCE_COUNT}: {display_name}...")

    # Define the instance launch details
    launch_details = oci.core.models.LaunchInstanceDetails(
        compartment_id=compartment_id,
        availability_domain=ad_name,
        shape=SHAPE,
        display_name=display_name,
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            source_type="image",
            image_id=win_image_id,
            boot_volume_size_in_gbs=BOOT_VOL_GBS,
        ),
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=OCPUS,
            memory_in_gbs=MEMORY_GBS,
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=subnet_id,
            assign_public_ip=True
        )
    )

    # Launch the instance
    resp = core.launch_instance(launch_details)
    instance_id = resp.data.id
    print(f"Launched instance '{display_name}' (OCID: {instance_id})")

    # Wait for the instance to reach RUNNING state
    print(f"Waiting for '{display_name}' to become RUNNING. This may take a few minutes...")
    oci.wait_until(
        core,
        core.get_instance(instance_id),
        'lifecycle_state',
        'RUNNING',
        max_wait_seconds=1800
    )
    print(f"Instance '{display_name}' is RUNNING.")

print("\nAll done! All Windows instances have been provisioned in the new compartment 'tempcomp'.")
